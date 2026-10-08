"""Calls counted since the bridge started: how many, how many failed, how fast.

Kept in memory only; the voice app stores history over time on its side.
"""

from __future__ import annotations

import time
from collections import deque
from datetime import UTC, datetime
from typing import Any

SAMPLES_PER_TOOL = 500
ERRORS_KEPT = 10


class Metrics:
    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.started = time.time()
        self._calls: dict[str, int] = {}
        self._errors: dict[str, int] = {}
        self._times: dict[str, deque[float]] = {}
        self._recent: deque[dict[str, Any]] = deque(maxlen=ERRORS_KEPT)

    def record(
        self, tool: str, ms: float, error: str | None = None, kind: str = "tool_error", trace_id: str | None = None
    ):
        self._calls[tool] = self._calls.get(tool, 0) + 1
        self._times.setdefault(tool, deque(maxlen=SAMPLES_PER_TOOL)).append(ms)
        if error is not None:
            self._errors[tool] = self._errors.get(tool, 0) + 1
            self._recent.appendleft(
                {
                    "at": datetime.now(UTC).isoformat(timespec="seconds"),
                    "tool": tool,
                    "kind": kind,
                    "trace_id": trace_id,
                    "error": error[:200],
                }
            )

    def snapshot(self) -> dict[str, Any]:
        tools = []
        for name in sorted(self._calls):
            times = sorted(self._times.get(name, ()))
            tools.append(
                {
                    "name": name,
                    "calls": self._calls[name],
                    "errors": self._errors.get(name, 0),
                    "p50_ms": _percentile(times, 0.50),
                    "p95_ms": _percentile(times, 0.95),
                }
            )
        return {"tools": tools, "errors": list(self._recent)}


def _percentile(sorted_times: list[float], q: float) -> int:
    if not sorted_times:
        return 0
    return round(sorted_times[min(int(q * len(sorted_times)), len(sorted_times) - 1)])


METRICS = Metrics()


def memory_rss_mb() -> int:
    """This process's resident memory, from /proc (Linux)."""
    try:
        for line in open("/proc/self/status"):
            if line.startswith("VmRSS:"):
                return round(int(line.split()[1]) / 1024)
    except OSError:
        pass
    return 0


def version() -> str:
    """The commit this bridge runs from, when it runs from a git checkout."""
    import subprocess
    from pathlib import Path

    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=Path(__file__).resolve().parent,
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
        return out.stdout.strip() or "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def restart_info(state_file: str, unit: str = "claude-voice") -> dict:
    """Whether systemd restarted this unit on its own since the last start we saw.

    A starting process cannot see why its predecessor stopped; systemd's count of
    automatic restarts (NRestarts) going up says it was not a deliberate start.
    """
    import os
    import subprocess
    from pathlib import Path

    at = datetime.now(UTC).isoformat(timespec="seconds")
    if not os.environ.get("INVOCATION_ID"):  # not started by systemd
        return {"at": at, "reason": None, "total": None}
    try:
        out = subprocess.run(
            ["systemctl", "--user", "show", unit, "-p", "NRestarts", "--value"],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
        total = int(out.stdout.strip() or 0)
    except (OSError, ValueError, subprocess.SubprocessError):
        return {"at": at, "reason": None, "total": None}
    path = Path(state_file).expanduser()
    try:
        seen = int(path.read_text())
    except (OSError, ValueError):
        seen = total
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(total))
    except OSError:
        pass
    return {"at": at, "reason": "auto-restart" if total > seen else None, "total": total}
