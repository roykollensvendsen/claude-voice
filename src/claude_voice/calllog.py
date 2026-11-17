"""The bridge's call log: one line per tool call, and daily totals per tool.

Every tool call passes the server's middleware, which calls `start` and `end` here. A
line keeps when, which tool, who asked (a claim, at most 64 characters), how it ended
and how long it took. It never keeps what was asked, the answer, or an error's message:
an error is one of five plain classes. The page that shows the log reads it through the
bridge (docs/call-log.md).

The lines live in ~/.local/state/claude-voice/calls.jsonl, the owner's alone, pruned to
90 days and 20,000 lines. The daily totals live in the bridge's database for two years,
counted in memory and flushed at most every 30 seconds; after a crash, today's lines
written since the last flush are counted again.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

log = logging.getLogger("claude_voice.calllog")

#: Tools a client calls over and over to stay current: counted in the totals, never written.
POLLING = frozenset({"whats_new", "get_active_session", "list_pending_approvals", "wait_for_session"})
#: The upper bound of each duration bucket in milliseconds; the last bucket is everything above.
BOUNDS = (50, 100, 200, 500, 1_000, 2_000, 5_000, 10_000, 20_000, 30_000, 60_000)
OUTCOMES = ("ok", "error", "refused", "timeout", "cancelled")
#: The outcomes that count as failures in the totals; refused and cancelled are their own.
FAILURES = frozenset({"error", "timeout"})
KEEP_DAYS = 90
MAX_LINES = 20_000
MAX_BYTES = 4 * 1024 * 1024
PRUNE_EVERY = 500
FLUSH_SECONDS = 30.0
TOTALS_YEARS = 2
CALLER_CHARS = 64
FILE = "calls.jsonl"
SHOWN_DAYS = 14  # the days the totals cover when the page names none
SPAN_DAYS = 400  # the most days one read of the totals may cover
DAY = re.compile(r"\d{4}-\d{2}-\d{2}")
#: What a call_start event and a running call carry; a call_end carries END.
START = ("id", "tool", "source", "caller", "at")
END = ("id", "tool", "outcome", "error_class", "ms", "ended_at")


class Totals(Protocol):
    def save_call_totals(self, rows: list[tuple[str, str, int, int, list[int]]], flushed_to: str) -> None: ...
    def call_totals(self, first: str, last: str) -> list[dict[str, Any]]: ...
    def call_totals_flushed_to(self) -> str: ...
    def drop_call_totals_before(self, day: str) -> None: ...


def _last_sunday(year: int, month: int) -> datetime:
    """01.00 UTC on the last Sunday of a month before December."""
    last = datetime(year, month + 1, 1, 1, tzinfo=UTC) - timedelta(days=1)
    return last - timedelta(days=(last.weekday() + 1) % 7)


def oslo_day(ts: float) -> str:
    """The date in Oslo at `ts`: one hour ahead of UTC, two in summer time, which runs from
    01.00 UTC on the last Sunday of March to 01.00 UTC on the last Sunday of October."""
    at = datetime.fromtimestamp(ts, UTC)
    summer = _last_sunday(at.year, 3) <= at < _last_sunday(at.year, 10)
    return (at + timedelta(hours=2 if summer else 1)).date().isoformat()


def _iso(ts: float) -> str:
    """A time as ISO 8601 in UTC, to the microsecond, so a line and a flush rarely share one."""
    return datetime.fromtimestamp(ts, UTC).isoformat(timespec="microseconds")


def _in_a_thread(fn: Callable[[], None]) -> None:
    def run() -> None:
        with suppress(Exception):  # a failed flush is logged, and tried again later
            fn()

    threading.Thread(target=run, name="call-totals", daemon=True).start()


def _ts(iso: str) -> float:
    return datetime.fromisoformat(iso).timestamp()


def a_day(text: str) -> str:
    """`text` if it is a date as YYYY-MM-DD; a ValueError otherwise."""
    # RULE: a day that is not a real date as YYYY-MM-DD is refused
    if not DAY.fullmatch(text):
        raise ValueError(f"not a day: {text[:20]!r}; give it as YYYY-MM-DD")
    date.fromisoformat(text)
    return text


def a_span(first: str, last: str) -> tuple[str, str]:
    """`first` and `last` if both are days at most SPAN_DAYS apart; a ValueError otherwise."""
    first, last = a_day(first), a_day(last)
    # RULE: a span of more than 400 days is refused
    if (date.fromisoformat(last) - date.fromisoformat(first)).days > SPAN_DAYS:
        raise ValueError(f"at most {SPAN_DAYS} days at a time")
    return first, last


def quantile(buckets: list[int], share: float) -> int | None:
    """The upper bound of the bucket that holds `share` of the calls; the last bucket's
    calls, over a minute, read as a minute. None when there are no calls."""
    total = sum(buckets)
    if not total:
        return None
    rank, seen = max(1, -(-share * total // 1)), 0
    for i, count in enumerate(buckets):
        seen += count
        if seen >= rank:
            return BOUNDS[min(i, len(BOUNDS) - 1)]
    return BOUNDS[-1]


def _ended_on(row: dict[str, Any], day: str) -> bool:
    """Whether a line ended on the Oslo day `day`; a line with no readable end did not."""
    try:
        return oslo_day(_ts(str(row["ended_at"]))) == day
    except (KeyError, ValueError):
        return False


def bucket(ms: float) -> int:
    """Which duration bucket `ms` falls in."""
    return next((i for i, bound in enumerate(BOUNDS) if ms <= bound), len(BOUNDS))


def classify(
    *, error: BaseException | None = None, refused: bool = False, cancelled: bool = False
) -> tuple[str, str | None]:
    """How a call ended, as (outcome, error class). Never the error's message."""
    if cancelled:
        return "cancelled", "caller_gone"
    if error is not None:
        if isinstance(error, TimeoutError) or "Timeout" in type(error).__name__:
            return "timeout", "timeout"
        if isinstance(error, ConnectionError):
            return "error", "no_answer"
        # RULE: a failed call keeps a plain error class never its message
        return "error", "bridge_failed"
    if refused:
        return "refused", "refused"
    return "ok", None


def caller_of(params: dict[str, Any]) -> str | None:
    """The calling session's name, as the call's own _meta claims it; never from its arguments."""
    meta = params.get("_meta")
    name = meta.get("caller") if isinstance(meta, dict) else None
    # RULE: the caller is a claim of at most 64 characters
    return str(name)[:CALLER_CHARS] if name else None


class CallLog:
    def __init__(
        self,
        path: Path,
        totals: Totals,
        clock: Callable[[], float] = time.time,
        *,
        max_lines: int = MAX_LINES,
        prune_every: int = PRUNE_EVERY,
        later: Callable[[Callable[[], None]], None] = _in_a_thread,
        publish: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> None:
        self.path = path.expanduser()
        self.totals = totals
        self.clock = clock
        self.max_lines = max_lines
        self.prune_every = prune_every
        self.later = later  # runs a flush away from the call that set it off
        self.publish = publish or (lambda event, data: None)  # the page's live events: api.HUB.publish
        self._lock = threading.Lock()  # the counts and the running calls
        self._file = threading.Lock()  # the file, where there is no flock
        self._flushing = threading.Lock()  # one flush at a time
        self._running: dict[str, dict[str, Any]] = {}
        self._counts: dict[tuple[str, str], list[Any]] = {}
        self._writes = 0
        self._flushed = clock()

    # -- start and end of a call -------------------------------------------------------

    def start(self, tool: str, source: str, caller: str | None) -> str:
        call_id = uuid.uuid4().hex[:12]
        with self._lock:
            self._running[call_id] = {
                "id": call_id,
                "tool": tool,
                "source": source,
                "caller": (caller or None) and caller[:CALLER_CHARS],
                "at": _iso(self.clock()),
            }
            shown = {key: self._running[call_id][key] for key in START}
        if tool not in POLLING:
            self.publish("call_start", shown)
        return call_id

    def end(self, call_id: str, outcome: str, error_class: str | None, ms: float) -> dict[str, Any] | None:
        """Count the call, and write its line unless its tool is a polling one."""
        with self._lock:
            now = self.clock()  # read under the lock, so a flush's copy either has this call or precedes it
            row = self._running.pop(call_id, None)
            if row is None:
                return None
            row = {**row, "ended_at": _iso(now), "outcome": outcome, "error_class": error_class, "ms": round(ms)}
            self._count(row, now)
        # RULE: a polling tool is counted but never written line by line
        if row["tool"] not in POLLING:
            self._append(row)
            # RULE: each call publishes its start and end but polling publishes nothing
            self.publish("call_end", {key: row[key] for key in END})
        if now - self._flushed >= FLUSH_SECONDS and not self._flushing.locked():
            self._flushed = now  # the next call does not start another
            # RULE: the totals are written off the calling thread
            self.later(self.flush)
        return row

    def running(self) -> list[dict[str, Any]]:
        """The calls not yet ended, polling ones left out, oldest first."""
        with self._lock:
            return [{key: row[key] for key in START} for row in self._running.values() if row["tool"] not in POLLING]

    # -- what the page reads -------------------------------------------------------------

    def totals_between(self, first: str, last: str, offered: list[str]) -> dict[str, Any]:
        """Calls, errors, median and p95 per (day, tool) from `first` to `last`, today's
        from memory; and the offered tools with no call in that window."""
        first, last = a_span(first, last)
        counts = {(row["day"], row["tool"]): row for row in self.totals.call_totals(first, last)}
        counts.update({(row["day"], row["tool"]): row for row in self._rows_in_memory() if first <= row["day"] <= last})
        rows = [
            {
                "day": day,
                "tool": tool,
                "calls": row["calls"],
                "errors": row["errors"],
                "median_ms": quantile(row["buckets"], 0.5),
                "p95_ms": quantile(row["buckets"], 0.95),
            }
            for (day, tool), row in sorted(counts.items())
        ]
        used = {row["tool"] for row in rows if row["calls"]}
        return {"today": oslo_day(self.clock()), "rows": rows, "unused": sorted(set(offered) - used)}

    def recent(self, limit: int) -> dict[str, Any]:
        """The last `limit` calls, newest first, and the calls running now."""
        return {"calls": self.read()[::-1][:limit], "running": self.running()}

    def day_calls(self, day: str, tool: str, offered: list[str]) -> dict[str, Any]:
        """Every line for `tool` that ended on the Oslo day `day`, oldest first."""
        day = a_day(day)
        lines = self._lines_of(tool)
        # RULE: a tool the bridge does not offer is refused
        if tool not in offered and not lines:  # a removed plugin's past days can still be opened
            raise ValueError(f"no tool here is called {tool[:64]!r}")
        return {
            "day": day,
            "tool": tool,
            "counted_only": tool in POLLING,
            "calls": [r for r in lines if _ended_on(r, day)],
        }

    def _lines_of(self, tool: str) -> list[dict[str, Any]]:
        """Every line in the file for `tool`, oldest first."""
        return [row for row in self.read() if isinstance(row, dict) and row.get("tool") == tool]

    def _rows_in_memory(self) -> list[dict[str, Any]]:
        with self._lock:
            return [
                {"day": day, "tool": tool, "calls": c[0], "errors": c[1], "buckets": list(c[2])}
                for (day, tool), c in self._counts.items()
            ]

    def _count(self, row: dict[str, Any], now: float) -> None:
        counts = self._counts.setdefault((oslo_day(now), row["tool"]), [0, 0, [0] * (len(BOUNDS) + 1)])
        counts[0] += 1
        # RULE: only errors and timeouts count as failures
        counts[1] += row["outcome"] in FAILURES
        counts[2][bucket(row["ms"])] += 1

    def today_rows(self) -> list[dict[str, Any]]:
        today = oslo_day(self.clock())
        return [row for row in self._rows_in_memory() if row["day"] == today]

    # -- the file -------------------------------------------------------------------------

    @contextmanager
    def _file_lock(self) -> Iterator[None]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # RULE: the file work is one writer at a time without a file lock
        with self._file:
            handle = os.open(self.path.with_name(self.path.name + ".lock"), os.O_RDWR | os.O_CREAT, 0o600)
            try:
                if sys.platform != "win32":  # Windows has no flock; the thread lock is all there is
                    import fcntl

                    fcntl.flock(handle, fcntl.LOCK_EX)
                yield
            finally:
                os.close(handle)

    def _append(self, row: dict[str, Any]) -> None:
        line = json.dumps(row, separators=(",", ":")) + "\n"
        with self._file_lock():
            # RULE: the call log is the owners alone
            handle = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            try:
                os.write(handle, line.encode("utf-8"))
            finally:
                os.close(handle)
            self._writes += 1
            big = self.path.stat().st_size > MAX_BYTES
        if self._writes % self.prune_every == 0 or big:
            self.prune()

    def read(self) -> list[dict[str, Any]]:
        """Every line in the file, oldest first; a damaged line is skipped."""
        try:
            text = self.path.read_text(encoding="utf-8")
        except OSError:
            return []
        out = []
        for line in text.splitlines():
            with suppress(ValueError):
                out.append(json.loads(line))
        return out

    def prune(self) -> None:
        """Keep the last 90 days, at most max_lines of them, by writing a new file and renaming it."""
        cutoff = _iso(self.clock() - KEEP_DAYS * 86400)
        with self._file_lock():
            kept = [row for row in self.read() if str(row.get("ended_at", "")) >= cutoff][-self.max_lines :]
            spare = self.path.with_name(self.path.name + ".new")
            handle = os.open(spare, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(handle, "w", encoding="utf-8") as out:
                out.writelines(json.dumps(row, separators=(",", ":")) + "\n" for row in kept)
            # RULE: old lines and lines beyond the cap are pruned through a rename
            os.replace(spare, self.path)

    # -- the totals ----------------------------------------------------------------------

    def flush(self) -> None:
        """Write the counts held in memory to the database, as of now."""
        with self._flushing:
            with self._lock:
                # RULE: a call counted while a flush copies is in the database or recovered
                now = self.clock()  # the time the copy is good to
                rows = [(day, tool, c[0], c[1], list(c[2])) for (day, tool), c in self._counts.items()]
                today = oslo_day(now)
                # copied above; an earlier day will not grow again
                cleared = {key: self._counts.pop(key) for key in [k for k in self._counts if k[0] != today]}
            try:
                self.totals.save_call_totals(rows, _iso(now))  # in the lines' own form, so they compare
            except Exception:
                # RULE: a failed save keeps the counts and is logged
                with self._lock:
                    for key, counts in cleared.items():
                        self._counts.setdefault(key, counts)
                log.warning("could not save the call totals; they are kept for the next try", exc_info=True)
                raise
            self._flushed = now

    def start_up(self) -> None:
        """At bridge start: prune, drop totals older than two years, and count today again."""
        now = self.clock()
        self.prune()
        oldest = datetime.fromisoformat(oslo_day(now)).date()
        self.totals.drop_call_totals_before(oldest.replace(year=oldest.year - TOTALS_YEARS).isoformat())
        today = oslo_day(now)
        flushed_to = self.totals.call_totals_flushed_to()
        since = oslo_day(_ts(flushed_to)) if flushed_to else today
        with self._lock:
            for row in self.totals.call_totals(min(since, today), today):
                self._counts[(row["day"], row["tool"])] = [row["calls"], row["errors"], list(row["buckets"])]
            # RULE: todays totals are rebuilt after a crash between flushes
            for row in self.read():
                ended = str(row.get("ended_at", ""))
                if ended > flushed_to:  # every day since, so the last calls before midnight count too
                    self._count(row, _ts(ended))
        self.flush()
