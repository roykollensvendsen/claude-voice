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
