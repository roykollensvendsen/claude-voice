"""The activity view: where sessions wait, burn tokens or get stuck.

Off by default. While it is off nothing is read: no transcript, no /proc, and the file is
only opened by the daily delete. While it is on, every ten seconds the bridge reads the bytes
each running session's transcript gained, and that session's processes in /proc. Kept are
numbers and kinds only, never what was said, asked or answered: per-minute counts in
~/.local/state/claude-voice/activity.db for seven days, and the running call in memory.

Which programmes are read at all is the owner's choice in the settings file
(`[activity] orgs`); a session in no programme is never read. Of those, only the programmes in
`phone_orgs` are served to a request that did not start on this computer.
"""

from __future__ import annotations

import functools
import json
import os
import re
import sqlite3
import sys
import threading
import time
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import IO, Any, Concatenate

from claude_agent_sdk import project_key_for_directory

__all__ = ["Activity", "ProcTree", "kind_of", "local_iso", "program_of", "project_key_for_directory"]

FILE = "activity.db"
SETTING = "activity"  # in the bridge's own store: when the switch turns itself off
PLAIN_ID = re.compile(r"[A-Za-z0-9_-]+")  # a session id names its transcript file
PROJECTS_DIR = Path("~/.claude/projects").expanduser()
INTERVAL = 10.0  # seconds between looks while on
FLUSH_SECONDS = 600.0  # minute rows go to disk this often, and whenever they are asked for
KEEP_SECONDS = 7 * 86400
DAY = 86400.0
DURATIONS = {"1h": 3600, "8h": 8 * 3600, "24h": 86400, "7d": 7 * 86400}
DEFAULT_DURATION = "8h"
WINDOWS = {"1h": (3600, 60), "6h": (6 * 3600, 300), "24h": (86400, 900), "7d": (7 * 86400, 3600)}
YELLOW_SECONDS = 5 * 60
RED_SECONDS = 10 * 60
STILL_SECONDS = 2 * 60  # a red call's CPU has not grown for this long
FIRST_LOOK_BYTES = 1 << 20  # of a transcript first seen, only its tail is read
TOKENS_IN = ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")  # all read
SEEN_MESSAGES = 512  # message ids remembered per session, to count each one once

PROGRAMS = ("git", "gh", "python", "pytest", "uv", "make", "cargo", "npm", "docker")
READS = frozenset({"Read", "Grep", "Glob", "LS"})
WRITES = frozenset({"Edit", "Write", "NotebookEdit", "MultiEdit"})
AGENTS = frozenset({"Agent", "Task"})
COUNTED = ("tok_in", "tok_out", "n_read", "n_write", "n_run", "n_mcp", "n_agent", "tool_ms", "cpu_ms")
STATUSES = ("busy_s", "idle_s", "waiting_s")
SUMMED = (*COUNTED, *STATUSES)
COLUMNS = (*COUNTED, "rss_mb_max", *STATUSES)
STATUS = {"busy": "busy", "shell": "busy", "idle": "idle", "waiting": "waiting"}

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS minutes (
    minute INTEGER NOT NULL, session TEXT NOT NULL, org TEXT NOT NULL, role TEXT NOT NULL,
    {", ".join(f"{c} INTEGER NOT NULL DEFAULT 0" for c in COLUMNS)},
    PRIMARY KEY (minute, session)
);
CREATE TABLE IF NOT EXISTS recording (start INTEGER PRIMARY KEY, until INTEGER NOT NULL);
"""


def _open(path: Path) -> IO[bytes]:
    """Every transcript and /proc file is opened here, so a test can see what was read."""
    return path.open("rb")


def local_iso(t: float) -> str:
    """A time as the page shows it: this computer's own time, with its offset."""
    return datetime.fromtimestamp(t).astimezone().isoformat(timespec="seconds")


def _ts(text: Any) -> float | None:
    try:
        return datetime.fromisoformat(str(text).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


# -- kinds and program names --------------------------------------------------------------


def kind_of(tool: str) -> str:
    """Which kind of call a tool is: read, write, run, mcp, agent or other."""
    if tool in READS:
        return "read"
    if tool in WRITES:
        return "write"
    if tool == "Bash":
        return "run"
    if tool.startswith("mcp__"):
        return "mcp"
    return "agent" if tool in AGENTS else "other"


def _first_word(command: str) -> str:
    for part in command.split("&&"):
        words = [w for w in part.split() if "=" not in w or w.startswith(("/", "."))]
        if words and words[0] != "cd":
            return words[0]
    return ""


def program_of(command: Any) -> str:
    """The program a command runs, from the fixed list, or "other": never its arguments."""
    name = os.path.basename(_first_word(str(command or "")))
    if name.startswith("python"):
        name = "python"
    # RULE: a program outside the fixed list is other
    return name if name in PROGRAMS else "other"


# -- /proc ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class Seen:
    """A session's processes at one look: CPU so far, memory now, its longest-running child."""

    cpu_ms: int
    rss_mb: int
    child: tuple[str, int] | None  # (program, age in seconds)


class ProcTree:
    """Reads a session's process and every process under it from /proc."""

    def __init__(self, root: Path = Path("/proc"), hz: int | None = None, page_kb: int | None = None) -> None:
        self.root = root
        self.hz, self.page_kb = hz or 0, page_kb or 0  # asked of the kernel at the first look

    def _units(self) -> bool:
        """The kernel's clock rate and page size; False where there is no /proc to read."""
        if not (self.hz and self.page_kb):
            # RULE: where there is no proc no process is read
            if sys.platform == "win32" or not self.root.is_dir():
                return False
            self.hz = self.hz or os.sysconf("SC_CLK_TCK")
            self.page_kb = self.page_kb or os.sysconf("SC_PAGE_SIZE") // 1024
        return True

    def _stat(self, pid: int) -> tuple[str, list[str]] | None:
        try:
            with _open(self.root / str(pid) / "stat") as f:
                text = f.read().decode(errors="replace")
        except OSError:
            return None
        head, _, rest = text.rpartition(")")
        return head.partition("(")[2], rest.split()

    def _listed(self, pid: int) -> list[int] | None:
        """The processes directly under `pid` from its threads' own lists; None on a kernel
        that keeps no such lists."""
        tasks = self.root / str(pid) / "task"
        try:
            lists = [tasks / t / "children" for t in sorted(os.listdir(tasks))]
        except OSError:
            return None
        if not lists or not lists[0].exists():
            return None
        found: list[int] = []
        for task in lists:
            try:
                with _open(task) as f:
                    found.extend(int(p) for p in f.read().split())
            except (OSError, ValueError):
                continue
        return found

    def _uptime(self) -> float:
        with _open(self.root / "uptime") as f:
            return float(f.read().split()[0])

    def sample(self, pids: Iterable[int]) -> dict[int, Seen]:
        """Each pid's own numbers summed with those of every process under it. On a kernel
        that keeps no lists of children, only the pid's own: finding them would mean reading
        every process on the machine, those of programmes that are not read among them."""
        wanted = list(pids)
        if not wanted or not self._units():
            return {}
        uptime = self._uptime()

        def children(pid: int) -> list[int]:
            # RULE: without children lists no other process is read
            return self._listed(pid) or []

        out = {}
        for pid in wanted:
            seen = self._seen(pid, uptime, children)
            if seen is not None:
                out[pid] = seen
        return out

    def _seen(self, pid: int, uptime: float, children: Callable[[int], list[int]]) -> Seen | None:
        tree: dict[int, tuple[str, list[str]]] = {}
        todo = [pid]
        while todo:
            here = todo.pop()
            stat = None if here in tree else self._stat(here)
            if stat is not None:
                tree[here] = stat
                todo.extend(children(here))
        if pid not in tree:
            return None
        # RULE: a sessions cpu includes its children
        ticks = sum(int(f[11]) + int(f[12]) for _, f in tree.values())
        rss_kb = sum(int(f[21]) for _, f in tree.values()) * self.page_kb
        below = [p for p in tree if p != pid]
        child = None
        if below:
            eldest = min(below, key=lambda p: int(tree[p][1][19]))
            age = int(uptime - int(tree[eldest][1][19]) / self.hz)
            child = (program_of(tree[eldest][0]), age)
        return Seen(ticks * 1000 // self.hz, rss_kb // 1024, child)


# -- reading a transcript ---------------------------------------------------------------------


@dataclass
class Call:
    kind: str
    tool: str
    program: str | None
    started: float


@dataclass
class Live:
    """What is known of one running session; numbers and kinds only."""

    name: str
    org: str
    role: str
    pid: int
    status: str = "idle"
    status_since: float | None = None
    turn_started: float | None = None
    calls: dict[str, Call] = field(default_factory=dict)
    offsets: dict[str, tuple[int, int]] = field(default_factory=dict)  # path: (inode, offset)
    tokens: dict[str, tuple[int, int]] = field(default_factory=dict)  # message id: counted
    cpu_ms: int | None = None
    cpu_grew: float = 0.0
    rss_mb: int = 0
    cpu_pct: float = 0.0
    child: tuple[str, int] | None = None
    looked: float | None = None


def _lines(path: Path, live: Live) -> Iterator[bytes]:
    """The whole lines `path` gained since the last look; a file that shrank or was replaced
    is read again from the start, and of one first seen only its tail is read."""
    try:
        info = path.stat()
    except OSError:
        return
    inode, offset = live.offsets.get(str(path), (info.st_ino, -1))
    # RULE: a transcript that shrank is read again from the start
    if inode != info.st_ino or info.st_size < offset:
        offset = 0
        live.calls = {}  # the calls it said were running are from the old file
    first = offset < 0
    if first:
        offset = max(info.st_size - FIRST_LOOK_BYTES, 0)
    with _open(path) as f:
        # RULE: only the bytes appended since the last look are read
        f.seek(offset)
        if first and offset:
            offset += len(f.readline())  # the rest of a line cut by the tail
        for raw in f:
            if not raw.endswith(b"\n"):
                break  # still being written: read next time
            offset += len(raw)
            yield raw
    live.offsets[str(path)] = (info.st_ino, offset)


class Minutes:
    """The counts not yet written, by minute and session."""

    def __init__(self) -> None:
        self.rows: dict[tuple[int, str], dict[str, Any]] = {}

    def add(self, at: float, live: Live, **counts: int) -> None:
        minute = int(at // 60 * 60)
        row = self.rows.setdefault((minute, live.name), {"org": live.org, "role": live.role})
        for key, value in counts.items():
            if key == "rss_mb_max":
                row[key] = max(row.get(key, 0), value)
            else:
                row[key] = row.get(key, 0) + value

    def take(self) -> dict[tuple[int, str], dict[str, Any]]:
        rows, self.rows = self.rows, {}
        return rows


def _usage(entry: dict[str, Any], live: Live, minutes: Minutes, at: float) -> None:
    message = entry.get("message") or {}
    usage, key = message.get("usage"), str(message.get("id") or "")
    if not isinstance(usage, dict) or not key:
        return
    t_in = sum(int(usage.get(k) or 0) for k in TOKENS_IN)
    t_out = int(usage.get("output_tokens") or 0)
    before_in, before_out = live.tokens.pop(key, (0, 0))
    # RULE: a messages tokens are counted once
    now_in, now_out = max(before_in, t_in), max(before_out, t_out)
    live.tokens[key] = (now_in, now_out)
    while len(live.tokens) > SEEN_MESSAGES:
        live.tokens.pop(next(iter(live.tokens)))
    minutes.add(at, live, tok_in=now_in - before_in, tok_out=now_out - before_out)


def _blocks(entry: dict[str, Any]) -> list[dict[str, Any]]:
    content = (entry.get("message") or {}).get("content")
    return [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []


def _use(block: dict[str, Any], live: Live, minutes: Minutes, at: float, counting: bool) -> None:
    tool = str(block.get("name") or "")
    kind = kind_of(tool)
    tool_input = block.get("input")
    program = program_of(tool_input.get("command")) if kind == "run" and isinstance(tool_input, dict) else None
    live.calls[str(block.get("id"))] = Call(kind, tool, program, at)
    if counting and kind != "other":
        minutes.add(at, live, **{f"n_{kind}": 1})


def _result(block: dict[str, Any], live: Live, minutes: Minutes, at: float, counting: bool) -> None:
    call = live.calls.pop(str(block.get("tool_use_id")), None)
    # RULE: a tool call lasts from its use to its result
    if call is not None and counting:
        minutes.add(at, live, tool_ms=max(int((at - call.started) * 1000), 0))


def _prompted(entry: dict[str, Any]) -> bool:
    if entry.get("type") != "user" or entry.get("isMeta"):
        return False
    content = (entry.get("message") or {}).get("content")
    return isinstance(content, str) or not any(b.get("type") == "tool_result" for b in _blocks(entry))


def _entry(entry: dict[str, Any], live: Live, minutes: Minutes, since: float, helper: bool) -> None:
    at = _ts(entry.get("timestamp"))
    if at is None:
        return
    counting = at >= since
    if entry.get("type") == "assistant":
        if counting:
            _usage(entry, live, minutes, at)
        for block in _blocks(entry):
            if block.get("type") == "tool_use":
                _use(block, live, minutes, at, counting)
    elif not helper and _prompted(entry):
        live.turn_started, live.calls = at, {}
    for block in _blocks(entry):
        if block.get("type") == "tool_result":
            _result(block, live, minutes, at, counting)


def read_transcript(path: Path, live: Live, minutes: Minutes, since: float, helper: bool = False) -> None:
    """Take in the lines a transcript gained: only numbers and kinds are kept."""
    for raw in _lines(path, live):
        try:
            entry = json.loads(raw)
        except ValueError:
            continue
        if isinstance(entry, dict):
            _entry(entry, live, minutes, since, helper)


# -- the file ----------------------------------------------------------------------------------


class Kept:
    """activity.db: per-minute counts and when recording was on, the owner's alone."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._db: sqlite3.Connection | None = None

    def exists(self) -> bool:
        return self._db is not None or self.path.exists()

    @property
    def db(self) -> sqlite3.Connection:
        if self._db is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            # RULE: the activity file is the owners alone
            os.close(os.open(self.path, os.O_WRONLY | os.O_CREAT, 0o600))
            self._db = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
            self._db.executescript(SCHEMA)
        return self._db

    def write(self, rows: dict[tuple[int, str], dict[str, Any]]) -> None:
        sums = ", ".join(f"{c}={c}+excluded.{c}" for c in SUMMED)
        sql = (
            f"INSERT INTO minutes (minute, session, org, role, {', '.join(COLUMNS)})"
            f" VALUES (?, ?, ?, ?, {', '.join('?' * len(COLUMNS))})"
            f" ON CONFLICT (minute, session) DO UPDATE SET {sums},"
            " rss_mb_max=MAX(rss_mb_max, excluded.rss_mb_max)"
        )
        values = [(m, s, r["org"], r["role"], *(int(r.get(c, 0)) for c in COLUMNS)) for (m, s), r in rows.items()]
        with self.db:
            self.db.executemany(sql, values)

    def record(self, start: float, until: float) -> None:
        self.db.execute(
            "INSERT INTO recording (start, until) VALUES (?, ?) ON CONFLICT (start) DO UPDATE SET until=excluded.until",
            (int(start), int(until)),
        )

    def purge(self, now: float) -> None:
        oldest = int(now - KEEP_SECONDS)
        with self.db:
            # RULE: rows older than seven days are deleted
            self.db.execute("DELETE FROM minutes WHERE minute < ?", (oldest,))
            self.db.execute("DELETE FROM recording WHERE until < ?", (oldest,))

    def forget_orgs(self, kept: Iterable[str]) -> None:
        kept = sorted(set(kept))
        with self.db:
            self.db.execute(f"DELETE FROM minutes WHERE org NOT IN ({', '.join('?' * len(kept))})", kept)

    def clear(self) -> None:
        with self.db:
            self.db.execute("DELETE FROM minutes")
            self.db.execute("DELETE FROM recording")

    def sessions(self, orgs: Iterable[str]) -> list[tuple[str, str, str]]:
        orgs = sorted(set(orgs))
        if not self.exists() or not orgs:
            return []
        sql = f"SELECT DISTINCT session, org, role FROM minutes WHERE org IN ({', '.join('?' * len(orgs))})"
        return [tuple(r) for r in self.db.execute(sql, orgs)]

    def buckets(self, where: str, args: list[Any], since: int, bucket: int) -> list[dict[str, Any]]:
        """Each bucket's sums, with memory as the sum of each session's own maximum."""
        cols = ", ".join(f"SUM({c})" for c in SUMMED)
        sql = (
            f"SELECT minute / {bucket} * {bucket} AS b, session, {cols}, MAX(rss_mb_max) FROM minutes"
            f" WHERE minute >= ? AND {where} GROUP BY b, session ORDER BY b"
        )
        out: dict[int, dict[str, Any]] = {}
        for row in self.db.execute(sql, [since, *args]):
            point = out.setdefault(row[0], dict.fromkeys(COLUMNS, 0))
            for name, value in zip((*SUMMED, "rss_mb_max"), row[2:], strict=True):
                point[name] += int(value or 0)
        return [{"t": b, **p} for b, p in out.items()]

    def spans(self, since: int) -> list[tuple[int, int]]:
        sql = "SELECT start, until FROM recording WHERE until >= ? ORDER BY start"
        return [(int(a), int(b)) for a, b in self.db.execute(sql, (since,))]


# -- the view ---------------------------------------------------------------------------------------


Place = Callable[[dict[str, Any]], "tuple[str, str] | None"]


def _one_at_a_time[**P, R](method: Callable[Concatenate[Activity, P], R]) -> Callable[Concatenate[Activity, P], R]:
    """The bridge looks in one worker thread and answers the page in others; they share
    the counts in memory and the file, so only one of them runs at a time."""

    @functools.wraps(method)
    def locked(self: Activity, /, *args: P.args, **kwargs: P.kwargs) -> R:
        # RULE: a look and a question never run at once
        with self.lock:
            return method(self, *args, **kwargs)

    return locked


class Activity:
    """The switch, the looks while it is on, and what the page is served."""

    def __init__(
        self,
        store: Any,
        path: Path,
        live: Callable[[], list[dict[str, Any]]],
        place: Place,
        orgs: Iterable[str] = (),
        phone_orgs: Iterable[str] = (),
        projects_dir: Path = PROJECTS_DIR,
        proc: ProcTree | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.store, self.live, self.place = store, live, place
        self.orgs, self.phone_orgs = frozenset(orgs), frozenset(phone_orgs) & frozenset(orgs)
        self.projects_dir, self.proc, self.clock = projects_dir, proc or ProcTree(), clock
        self.kept = Kept(path)
        self.lock = threading.RLock()  # a look calls flush and daily while it holds it
        self.sessions: dict[str, Live] = {}
        self.minutes = Minutes()
        self.since: float | None = None  # when this stretch of recording began
        self.flushed = self.purged = self.looked = 0.0
        if self.kept.exists():  # at start: old rows go, and a programme no longer read loses its rows
            self.kept.purge(self.clock())
            # RULE: a programme switched off loses its rows
            self.kept.forget_orgs(self.orgs)
            self.purged = self.clock()

    # -- the switch ------------------------------------------------------------------------

    def _until(self) -> float | None:
        until = (self.store.get_setting(SETTING) or {}).get("until")
        return float(until) if isinstance(until, (int, float)) else None

    @_one_at_a_time
    def is_on(self) -> bool:
        until = self._until()
        if until is None:
            return False
        # RULE: the switch turns itself off when its time ends
        if self.clock() >= until:
            self._stop(until)
            return False
        return True

    @_one_at_a_time
    def switch(self, body: Any) -> None:
        """Turn it on for one of the durations, or off; ValueError says what does not fit."""
        if not isinstance(body, dict) or not isinstance(body.get("on"), bool) or set(body) - {"on", "for"}:
            raise ValueError('send {"on": true, "for": "8h"} or {"on": false}')
        if not body["on"]:
            if self.is_on():
                self._stop(self.clock())
            return
        duration = body.get("for", DEFAULT_DURATION)
        if duration not in DURATIONS:
            raise ValueError(f"for is one of: {', '.join(DURATIONS)}")
        self.store.set_setting(SETTING, {"until": self.clock() + DURATIONS[duration]})
        if self.since is None:
            self.since = self.clock()  # counted from the switch, not from the first look

    def _stop(self, at: float) -> None:
        self.store.set_setting(SETTING, {"until": None})
        if self.since is not None:
            self.flush(at)
        self.sessions, self.since = {}, None
        if self.kept.exists():
            self.kept.purge(self.clock())

    @_one_at_a_time
    def state(self, local: bool) -> dict[str, Any]:
        on = self.is_on()
        shown = self._shown(local)
        names = {s.name: (s.org, s.role) for s in self.sessions.values() if s.org in shown}
        for name, org, role in self.kept.sessions(shown):
            names.setdefault(name, (org, role))
        return {
            "on": on,
            "until": local_iso(u) if on and (u := self._until()) is not None else None,
            "durations": list(DURATIONS),
            "scopes": {
                "orgs": sorted(shown),
                "sessions": [{"name": n, "org": o, "role": r} for n, (o, r) in sorted(names.items())],
            },
        }

    # -- looking ---------------------------------------------------------------------------------

    @_one_at_a_time
    def tick(self) -> None:
        """One look, every ten seconds while on; while off only the daily delete may run."""
        # RULE: activity reads nothing while off
        if not self.is_on():  # only the daily delete, which opens nothing but the file
            self.daily()
            return
        now = self.clock()
        if now - self.looked < INTERVAL - 1:
            return  # the bridge's watcher comes by more often than a look is wanted
        self.looked = now
        if self.since is None:
            self.since = now
        rows = self._readable()
        self.sessions = {n: self.sessions.get(n) or Live(n, *p, int(r["pid"])) for n, (r, p) in rows.items()}
        seen = self.proc.sample(s.pid for s in self.sessions.values())
        for name, (row, _) in rows.items():
            self._look(self.sessions[name], row, seen.get(self.sessions[name].pid), now)
        if now - self.flushed >= FLUSH_SECONDS:
            self.flush(now)
        self.daily()

    def _readable(self) -> dict[str, tuple[dict[str, Any], tuple[str, str]]]:
        """The running sessions this view may read, by name, each with its programme and role."""
        rows = {}
        for row in self.live():
            # RULE: a session row that is not plain is skipped
            if not (str(row.get("pid") or "").isdigit() and PLAIN_ID.fullmatch(str(row.get("sessionId") or ""))):
                continue  # no process to read, or an id that could reach outside the transcripts
            placed = self.place(row)
            # RULE: a programme not switched on is never read
            if placed is None or placed[0] not in self.orgs:
                continue
            rows[str(row.get("name") or row.get("sessionId"))] = (row, placed)
        return rows

    def _look(self, live: Live, row: dict[str, Any], seen: Seen | None, now: float) -> None:
        since = self.since or now
        sid, cwd = str(row.get("sessionId")), str(row.get("cwd") or "")
        main = self.projects_dir / project_key_for_directory(cwd) / f"{sid}.jsonl"
        read_transcript(main, live, self.minutes, since)
        for helper in sorted((main.parent / sid / "subagents").glob("*.jsonl")):
            read_transcript(helper, live, self.minutes, since, helper=True)
        status = STATUS.get(str(row.get("status")), "busy")
        if live.looked is not None:
            self.minutes.add(now, live, **{f"{live.status}_s": int(round(now - live.looked))})
        if status != live.status or live.status_since is None:
            live.status, live.status_since = status, _ms(row.get("statusUpdatedAt")) or now
        if seen is not None:
            self._cpu(live, seen, now)
        live.looked = now

    def _cpu(self, live: Live, seen: Seen, now: float) -> None:
        grew = 0 if live.cpu_ms is None else max(seen.cpu_ms - live.cpu_ms, 0)
        if live.cpu_ms is None or grew:
            live.cpu_grew = now
        if live.looked is not None and now > live.looked:
            live.cpu_pct = round(100.0 * grew / ((now - live.looked) * 1000), 1)
        live.cpu_ms, live.rss_mb, live.child = seen.cpu_ms, seen.rss_mb, seen.child
        self.minutes.add(now, live, cpu_ms=grew, rss_mb_max=seen.rss_mb)

    @_one_at_a_time
    def flush(self, now: float | None = None) -> None:
        """Write the counts so far, and how long recording has been on."""
        now = self.clock() if now is None else now
        rows = self.minutes.take()
        if rows or self.since is not None:
            self.kept.write(rows)
        if self.since is not None:
            self.kept.record(self.since, now)
        self.flushed = now

    @_one_at_a_time
    def daily(self) -> None:
        """Rows older than seven days go once a day, on or off; only the file is opened."""
        now = self.clock()
        if now - self.purged >= DAY and self.kept.exists():
            self.kept.purge(now)
            self.purged = now

    @_one_at_a_time
    def clear(self) -> None:
        self.minutes.take()
        self.kept.clear()
        if self.since is not None:
            self.since = self.clock()

    # -- serving --------------------------------------------------------------------------------

    def _shown(self, local: bool) -> frozenset[str]:
        # RULE: a programme kept off the phone is served only here
        return self.orgs if local else self.phone_orgs

    def _scope(self, scope: str, local: bool) -> tuple[str, list[Any], Callable[[Live], bool]] | None:
        shown = sorted(self._shown(local))
        if not shown:
            return None
        within = f"org IN ({', '.join('?' * len(shown))})"
        kind, _, value = scope.partition(":")
        if scope == "all":
            return within, shown, lambda s: s.org in shown
        if kind == "org" and value in shown:
            return "org = ?", [value], lambda s: s.org == value
        if kind == "session" and self._known(value, shown):
            return f"session = ? AND {within}", [value, *shown], lambda s: s.name == value and s.org in shown
        return None

    def _known(self, name: str, shown: list[str]) -> bool:
        if any(s.name == name and s.org in shown for s in self.sessions.values()):
            return True
        return any(n == name for n, _, _ in self.kept.sessions(shown))

    @_one_at_a_time
    def series(self, scope: str, window: str, local: bool) -> dict[str, Any] | None:
        """Counts per bucket over a window; None for a scope this caller may not see."""
        if window not in WINDOWS:
            raise ValueError(f"window is one of: {', '.join(WINDOWS)}")
        if self.is_on():
            self.flush()
        found = self._scope(scope, local)
        if found is None:
            return None
        seconds, bucket = WINDOWS[window]
        now = self.clock()
        start = int(now - seconds)
        points = self.kept.buckets(found[0], found[1], start // bucket * bucket, bucket) if self.kept.exists() else []
        spans = self.kept.spans(start) if self.kept.exists() else []
        return {
            "scope": scope,
            "window": window,
            "bucket_s": bucket,
            "from": local_iso(start),
            "to": local_iso(now),
            "recording": [{"from": local_iso(max(a, start)), "to": local_iso(b)} for a, b in spans],
            "points": [{**p, "t": local_iso(p["t"])} for p in points],
        }

    @_one_at_a_time
    def now(self, scope: str, local: bool) -> dict[str, Any] | None:
        """What each session is doing right now, from memory; None while off."""
        if not self.is_on():
            return None
        found = self._scope(scope, local)
        if found is None:
            return None
        now = self.clock()
        rows = [self._row(s, now) for s in sorted(self.sessions.values(), key=lambda s: s.name) if found[2](s)]
        return {"at": local_iso(now), "sessions": rows}

    @staticmethod
    def _state(call: Call | None, live: Live, now: float) -> str:
        age = now - call.started if call else 0.0
        # RULE: a long call whose cpu stands still is red
        if age >= RED_SECONDS and now - live.cpu_grew >= STILL_SECONDS:
            return "red"
        return "yellow" if age >= YELLOW_SECONDS else "ok"

    def _row(self, live: Live, now: float) -> dict[str, Any]:
        call = max(live.calls.values(), key=lambda c: -c.started, default=None)
        return {
            "name": live.name,
            "org": live.org,
            "role": live.role,
            "status": live.status,
            "status_since": local_iso(live.status_since) if live.status_since else None,
            "call": None
            if call is None
            else {"kind": call.kind, "tool": call.tool, "program": call.program, "started": local_iso(call.started)},
            "turn_started": local_iso(live.turn_started) if live.turn_started else None,
            "state": self._state(call, live, now),
            "cpu_pct": live.cpu_pct,
            "rss_mb": live.rss_mb,
            "child": None if live.child is None else {"program": live.child[0], "age_s": live.child[1]},
        }


def _ms(value: Any) -> float | None:
    """Claude Code's statusUpdatedAt, in milliseconds, as seconds."""
    return float(value) / 1000 if isinstance(value, (int, float)) and value > 0 else None
