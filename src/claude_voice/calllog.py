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
import os
import re
import sys
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

#: Tools a client calls over and over to stay current: counted in the totals, never written.
POLLING = frozenset({"whats_new", "get_active_session", "list_pending_approvals", "wait_for_session"})
#: A phone's own tools the voice calls over and over, after the phone's machine name.
PHONE_POLLING = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]*_(?:sms_since|sms_send_outcomes)")
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
    return datetime.fromtimestamp(ts, UTC).isoformat(timespec="milliseconds")


def _ts(iso: str) -> float:
    return datetime.fromisoformat(iso).timestamp()


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


def polling(tool: str) -> bool:
    """Whether a client calls `tool` over and over to stay current: one of the bridge's own, or a
    phone's `sms_since` or `sms_send_outcomes` after its machine name."""
    # RULE: a phones sms_since and sms_send_outcomes count as polling
    return tool in POLLING or PHONE_POLLING.fullmatch(tool) is not None


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
    ) -> None:
        self.path = path.expanduser()
        self.totals = totals
        self.clock = clock
        self.max_lines = max_lines
        self.prune_every = prune_every
        self._lock = threading.Lock()
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
        return call_id

    def end(self, call_id: str, outcome: str, error_class: str | None, ms: float) -> dict[str, Any] | None:
        """Count the call, and write its line unless its tool is a polling one."""
        now = self.clock()
        with self._lock:
            row = self._running.pop(call_id, None)
            if row is None:
                return None
            row = {**row, "ended_at": _iso(now), "outcome": outcome, "error_class": error_class, "ms": round(ms)}
            self._count(row, now)
        # RULE: a polling tool is counted but never written line by line
        if not polling(row["tool"]):
            self._append(row)
        if now - self._flushed >= FLUSH_SECONDS:
            self.flush()
        return row

    def running(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._running.values())

    def _count(self, row: dict[str, Any], now: float) -> None:
        counts = self._counts.setdefault((oslo_day(now), row["tool"]), [0, 0, [0] * (len(BOUNDS) + 1)])
        counts[0] += 1
        # RULE: only errors and timeouts count as failures
        counts[1] += row["outcome"] in FAILURES
        counts[2][bucket(row["ms"])] += 1

    def today_rows(self) -> list[dict[str, Any]]:
        today = oslo_day(self.clock())
        with self._lock:
            return [
                {"day": day, "tool": tool, "calls": c[0], "errors": c[1], "buckets": list(c[2])}
                for (day, tool), c in self._counts.items()
                if day == today
            ]

    # -- the file -------------------------------------------------------------------------

    @contextmanager
    def _file_lock(self) -> Iterator[None]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = os.open(self.path.with_name(self.path.name + ".lock"), os.O_RDWR | os.O_CREAT, 0o600)
        try:
            if sys.platform != "win32":  # Windows has no flock; one bridge process writes there
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
        now = self.clock()
        with self._lock:
            rows = [(day, tool, c[0], c[1], list(c[2])) for (day, tool), c in self._counts.items()]
        self.totals.save_call_totals(rows, _iso(now))  # rounded as the lines are, so they compare
        self._flushed = now
        today = oslo_day(now)
        with self._lock:  # an earlier day is in the database now, and will not grow again
            for key in [key for key in self._counts if key[0] != today]:
                del self._counts[key]

    def start_up(self) -> None:
        """At bridge start: prune, drop totals older than two years, and count today again."""
        now = self.clock()
        self.prune()
        oldest = datetime.fromisoformat(oslo_day(now)).date()
        self.totals.drop_call_totals_before(oldest.replace(year=oldest.year - TOTALS_YEARS).isoformat())
        today = oslo_day(now)
        with self._lock:
            for row in self.totals.call_totals(today, today):
                self._counts[(today, row["tool"])] = [row["calls"], row["errors"], list(row["buckets"])]
        flushed_to = self.totals.call_totals_flushed_to()
        # RULE: todays totals are rebuilt after a crash between flushes
        for row in self.read():
            ended = str(row.get("ended_at", ""))
            if ended > flushed_to and oslo_day(_ts(ended)) == today:
                with self._lock:
                    self._count(row, _ts(ended))
        self._flushed = now
