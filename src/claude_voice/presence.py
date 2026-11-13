"""Whether the owner is away from the laptop, so a session's question should wait for the phone.

Waiting for the phone while he sits at the laptop delays the box he would answer at once,
and not waiting while he is away sends him walking back (ADR-CV-008). He counts as away
while the companion app is open, while the screen is locked, for ten minutes after the
voice called the bridge, and after five minutes without keyboard or mouse.
"""

from __future__ import annotations

import os
import re
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass

VOICE_WINDOW = 600.0  # seconds after a voice call that he still counts as away
IDLE_LIMIT = 300.0  # seconds without keyboard or mouse that count as away


@dataclass(frozen=True)
class Desktop:
    """What the desktop says: whether the screen is locked, and for how long nobody touched it."""

    locked: bool
    idle_seconds: float


def _run(command: list[str]) -> str:
    return subprocess.run(command, capture_output=True, text=True, check=True, timeout=3).stdout  # noqa: S603


def read_desktop(run: Callable[[list[str]], str] = _run) -> Desktop:
    """The lock from logind, and the idle time from GNOME's idle monitor; OSError when unreadable."""
    try:
        # Windows has no user id and no loginctl; the missing command then says "unreadable".
        user = os.environ.get("USER") or str(getattr(os, "getuid", str)())
        session = run(["loginctl", "show-user", user, "--property=Display", "--value"]).strip()
        locked = run(["loginctl", "show-session", session, "-p", "LockedHint", "--value"]).strip() == "yes"
        idle = run(
            [
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
        )
    except (subprocess.SubprocessError, FileNotFoundError) as exc:
        raise OSError(str(exc)) from None
    found = re.search(r"uint64 (\d+)", idle)
    return Desktop(locked=locked, idle_seconds=int(found.group(1)) / 1000 if found else 0.0)


class Presence:
    """Remembers when the voice last called, and asks the desktop the rest."""

    def __init__(
        self, desktop: Callable[[], Desktop] = read_desktop, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self.desktop = desktop
        self.clock = clock
        self.voice_at: float | None = None

    def heard_voice(self) -> None:
        """The voice called the bridge just now."""
        self.voice_at = self.clock()

    def away(self, *, app_open: bool) -> bool:
        """Whether a question should wait for the phone."""
        if app_open:
            return True
        # RULE: the owner counts as away for ten minutes after the voice called
        if self.voice_at is not None and self.clock() - self.voice_at < VOICE_WINDOW:
            return True
        try:
            desk = self.desktop()
        except OSError:
            return False  # unknown is treated as at the laptop, which is how it behaved before
        # RULE: the owner is away while the screen is locked
        return desk.locked or desk.idle_seconds > IDLE_LIMIT
