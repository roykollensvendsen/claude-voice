"""Builders left idle while work waits: noticed once a minute, said once per episode.

The owner's rule is that a builder never sits idle while its programme's work list has items.
Each programme names its builders and its list in its org file (docs/org-chart.md, "Idle
builders"). When every builder has been idle for the programme's wait and an item is neither
taken, done nor blocked, that is one episode: the manager is told at once, and the voice too
when the programme says so, held at night until the morning. The episode ends when a builder
works again or nothing waits; only then can the next one be flagged.

Only the programme's name and the two counts ever leave here, never an item's text.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from . import backlog

if TYPE_CHECKING:  # the chart is handed in; importing it here would put this above it
    from .org import Chart, Place, Programme, Role

EVERY = 60.0  # seconds between two looks
NIGHT = (23, 7)  # from this hour until that one, the voice's item waits for the morning


@dataclass(frozen=True)
class Flag:
    programme: Programme
    counts: backlog.Counts

    def to_manager(self) -> str:
        p = self.programme
        return f"All builders in {p.name} idle {p.idle_after_min} min; {self.counts.said()}."

    def to_voice(self) -> str:
        p = self.programme
        return f"{p.name}: all builders idle {p.idle_after_min} minutes with {self.counts.said()}."


def _local_hour(ts: float) -> int:
    return time.localtime(ts).tm_hour


def _idle_long(row: dict[str, Any], since: float) -> bool:
    """Whether Claude Code has shown the session as idle since before `since`."""
    # RULE: a builder waiting on a prompt is not idle
    if row.get("status") != "idle":
        return False
    changed = row.get("statusUpdatedAt")
    if not isinstance(changed, int | float) or isinstance(changed, bool):
        return False  # no time to go by, so not known to be idle that long
    return changed / 1000 <= since


class Watch:
    """Remembers the episodes flagged while the bridge runs, and the voice's held items."""

    def __init__(
        self, state: Path, clock: Callable[[], float] = time.time, hour: Callable[[float], int] = _local_hour
    ) -> None:
        self.state = state
        self.clock = clock
        self.hour = hour
        self.last: float | None = None
        self.flagged: set[str] = set()
        self.held: dict[str, Flag] = {}

    def again(self, code: str) -> None:
        """Flag programme `code`'s episode once more at the next look: its message never left."""
        self.flagged.discard(code)

    def _night(self, now: float) -> bool:
        start, end = NIGHT
        hour = self.hour(now)
        return hour >= start or hour < end

    def _all_idle(self, p: Programme, rows: list[dict[str, Any]], place: Callable[[dict[str, Any]], Place]) -> bool:
        builders = p.builders()
        if not builders:
            return False
        since = self.clock() - p.idle_after_min * 60

        def holders(role: Role) -> list[dict[str, Any]]:
            return [d for d in rows if (at := place(d)).programme is p and at.role is role]

        # RULE: a builder role with no running session counts as idle
        return all(all(_idle_long(d, since) for d in holders(role)) for role in builders)

    def _waiting(self, p: Programme) -> backlog.Counts | None:
        """The work list's counts when an item waits for a builder; None otherwise. Raises
        OSError when the list cannot be read just now."""
        if not p.backlog:
            return None
        try:
            counts = backlog.read(self.state, p.backlog)
        except backlog.Outside:
            return None  # org check says why
        return counts if counts.waiting else None

    def look(
        self, chart: Chart, rows: list[dict[str, Any]], place: Callable[[dict[str, Any]], Place]
    ) -> tuple[list[Flag], list[Flag]]:
        """What to tell the managers now, and what to tell the voice now."""
        now = self.clock()
        if self.last is not None and now - self.last < EVERY:
            return [], []
        self.last = now
        managers: list[Flag] = []
        voice: list[Flag] = []
        for p in chart.programmes:
            try:
                counts = self._waiting(p) if self._all_idle(p, rows, place) else None
            except OSError:
                # RULE: a work list that cannot be read for a moment keeps the episode
                continue  # the episode stays as it stands
            if counts is None:
                # RULE: an episode ends when a builder works or nothing waits
                self.flagged.discard(p.code)
                self.held.pop(p.code, None)
                continue
            # RULE: all builders idle with an item waiting is flagged once per episode
            if p.code in self.flagged:
                continue
            self.flagged.add(p.code)
            flag = Flag(p, counts)
            managers.append(flag)
            if p.idle_report:
                self.held[p.code] = flag
        # RULE: the voice hears of idle builders at night only in the morning
        if not self._night(now):
            voice, self.held = list(self.held.values()), {}
        return managers, voice
