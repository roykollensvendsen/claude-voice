"""A made-up gizmo that refuses what the real one refuses.

A fake that says yes to everything lets a test pass that the real device would fail. So this
one keeps the device's limits: it answers at most `READS_PER_MINUTE` times a minute and then
says how long to wait, and it can be told to stop answering, as a gizmo off the network does.
Write each new limit of the real device in here first, with a test, before the code that
copes with it.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from gizmo_mcp.device import Reading, SlowDownError, UnreachableError

if TYPE_CHECKING:
    from collections.abc import Callable

#: The made-up device's own limit on reads.
READS_PER_MINUTE = 6
WINDOW = 60.0


class FakeGizmo:
    """A gizmo in memory, with the real one's limits."""

    def __init__(
        self,
        *,
        on: bool = True,
        level: int = 40,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Start the fake in a known state; `clock` lets a test move time on."""
        self.on = on
        self.level = level
        self.unreachable = False
        self._clock = clock
        self._reads: list[float] = []

    async def read(self) -> Reading:
        """The state, unless the gizmo is off the network or was asked too often."""
        if self.unreachable:
            msg = "the gizmo did not answer"
            raise UnreachableError(msg)
        now = self._clock()
        self._reads = [at for at in self._reads if now - at < WINDOW]
        # RULE: the fake refuses a read sooner than the device allows
        if len(self._reads) >= READS_PER_MINUTE:
            raise SlowDownError(WINDOW - (now - self._reads[0]))
        self._reads.append(now)
        return Reading(on=self.on, level=self.level)
