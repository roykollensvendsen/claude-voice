"""Calls counted since the bridge started: how many, how many failed, how fast.

Kept in memory only; the voice app stores history over time on its side.
"""

from __future__ import annotations

import contextlib
import time
from collections import deque
from datetime import UTC, datetime
from typing import Any

import psutil

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
    """This process's resident memory, as the operating system reports it."""
    try:
        return round(psutil.Process().memory_info().rss / (1024 * 1024))
    except psutil.Error:
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
        return out.stdout.strip() or installed_version()
    except (OSError, subprocess.SubprocessError):
        return installed_version()


def installed_version() -> str:
    """The version an installed copy was built as, with its commit when pip recorded one."""
    import json
    from importlib import metadata

    try:
        dist = metadata.distribution("claude-voice")
    except metadata.PackageNotFoundError:
        return "unknown"
    with contextlib.suppress(ValueError, TypeError, AttributeError):
        commit = json.loads(dist.read_text("direct_url.json") or "{}").get("vcs_info", {}).get("commit_id")
        if commit:
            return str(commit)[:7]
    return dist.version
