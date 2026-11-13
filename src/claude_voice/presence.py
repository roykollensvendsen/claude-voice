"""Whether the owner is away from the laptop, so a session's question should wait for the phone.

Waiting for the phone while he sits at the laptop delays the box he would answer at once,
and not waiting while he is away sends him walking back (ADR-CV-008). He counts as away
while the companion app is open, while the screen is locked, after five minutes without
keyboard or mouse, and for ten minutes after a voice call, from the voice app or the Claude
app, unless he has used the keyboard or mouse since.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass

log = logging.getLogger(__name__)

VOICE_WINDOW = 600.0  # seconds after a voice call that he still counts as away
IDLE_LIMIT = 300.0  # seconds without keyboard or mouse that count as away
DESK_CACHE = 3.0  # seconds one reading of the desktop is trusted for
IDLE_MONITOR = [
    "gdbus",
    "call",
    "--session",
    "--dest",
    "org.gnome.Mutter.IdleMonitor",
    "--object-path",
    "/org/gnome/Mutter/IdleMonitor/Core",
    "--method",
    "org.gnome.Mutter.IdleMonitor.GetIdletime",
]
_REPORTED: set[str] = set()  # the desktop sources already logged as unreadable


@dataclass(frozen=True)
class Desktop:
    """What the desktop says: whether the screen is locked, and for how long nobody touched it.

    Either is None when the desktop cannot tell it."""

    locked: bool | None
    idle_seconds: float | None


UNREADABLE = Desktop(locked=None, idle_seconds=None)


def _run(command: list[str]) -> str:
    return subprocess.run(command, capture_output=True, text=True, check=True, timeout=3).stdout  # noqa: S603


def _locked(run: Callable[[list[str]], str]) -> bool:
    # Windows has no user id and no loginctl; the missing command then says "unreadable".
    user = os.environ.get("USER") or str(getattr(os, "getuid", str)())
    session = run(["loginctl", "show-user", user, "--property=Display", "--value"]).strip()
    return run(["loginctl", "show-session", session, "-p", "LockedHint", "--value"]).strip() == "yes"


def _idle(run: Callable[[list[str]], str]) -> float:
    found = re.search(r"uint64 (\d+)", run(IDLE_MONITOR))
    if not found:
        raise ValueError("the idle monitor gave no idle time")
    return int(found.group(1)) / 1000


def _source[T](name: str, read: Callable[[], T]) -> T | None:
    """One source's reading, or None when it cannot be read; that is logged once per source."""
    try:
        return read()
    except (subprocess.SubprocessError, OSError, ValueError) as exc:
        # RULE: an unreadable desktop source is logged once
        if name not in _REPORTED:
            _REPORTED.add(name)
            log.warning("the desktop cannot tell %s, so it is not counted: %s", name, exc)
        return None


def read_desktop(run: Callable[[list[str]], str] = _run) -> Desktop:
    """The lock from logind, and the idle time from GNOME's idle monitor, each read on its own."""
    # RULE: a desktop source that cannot be read leaves the other one counted
    locked = _source("whether the screen is locked", lambda: _locked(run))
    idle = _source("how long nobody touched it", lambda: _idle(run))
    return Desktop(locked=locked, idle_seconds=idle)


class Presence:
    """Remembers when the voice last called, and asks the desktop the rest."""

    def __init__(
        self,
        desktop: Callable[[], Desktop] = read_desktop,
        clock: Callable[[], float] = time.monotonic,
        cache: float = DESK_CACHE,
    ) -> None:
        self.desktop = desktop
        self.clock = clock
        self.cache = cache
        self.voice_at: float | None = None
        self._desk: tuple[float, Desktop] | None = None  # when the desktop was last read, and what it said

    def heard_voice(self) -> None:
        """The voice called the bridge just now."""
        self.voice_at = self.clock()

    def _read(self) -> Desktop:
        now = self.clock()
        # RULE: the desktop is read at most once every few seconds
        if self._desk is not None and now - self._desk[0] < self.cache:
            return self._desk[1]
        try:
            desk = self.desktop()
        except OSError:
            desk = UNREADABLE  # unknown is treated as at the laptop, which is how it behaved before
        self._desk = (now, desk)
        return desk

    def away(self, *, app_open: bool) -> bool:
        """Whether a question should wait for the phone. It may block for a few seconds."""
        if app_open:
            return True
        desk = self._read()
        # RULE: the owner is away while the screen is locked
        if desk.locked:
            return True
        idle = desk.idle_seconds
        # RULE: the owner is away after five minutes without keyboard or mouse
        if idle is not None and idle > IDLE_LIMIT:
            return True
        if self.voice_at is None:
            return False
        since = self.clock() - self.voice_at
        # RULE: the owner counts as away for ten minutes after the voice called
        if since < VOICE_WINDOW:
            # RULE: keyboard or mouse used since the voice called counts as at the laptop
            return not (idle is not None and idle < since)
        return False
