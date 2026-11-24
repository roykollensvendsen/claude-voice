"""The shared temporary disk, watched so it never fills up unseen (#434).

Every session on the machine writes its scratch files, worktrees and test output to one
temporary disk, often held in memory and small. On 21.11 it filled, and every session
stopped. The bridge measures it every ten minutes, and only reads: nothing is deleted.
A session that made something removes it itself, as the rules already say.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

#: Past this share used, the largest folders are named.
WARN = 80
#: Past this share used, the voice is told: little room is left before sessions stop.
FULL = 90
#: Seconds between two measures. A walk of a full disk takes a moment.
EVERY = 600
#: How many of the largest folders are named.
TOP = 5


def sessions_root() -> Path:
    """The folder Claude Code sessions keep their scratch files in, on the temporary disk."""
    uid = os.getuid() if hasattr(os, "getuid") else None  # Windows has no user number
    return Path(tempfile.gettempdir()) / (f"claude-{uid}" if uid is not None else "claude")


def size_of(folder: Path) -> int:
    """The bytes a folder holds on the disk, counted as `du` counts them.

    A link is not followed, so a link to a big folder elsewhere counts nothing. Whatever
    vanishes or can't be read while it is walked counts nothing either: sessions keep writing.
    """
    total, todo = 0, [folder]
    while todo:
        try:
            entries = list(os.scandir(todo.pop()))
        except OSError:
            continue
        for entry in entries:
            try:
                st = entry.stat(follow_symlinks=False)
            except OSError:
                continue
            blocks = getattr(st, "st_blocks", None)  # Windows has no block count
            total += blocks * 512 if blocks is not None else st.st_size
            if entry.is_dir(follow_symlinks=False):
                todo.append(Path(entry.path))
    return total


def largest(root: Path, top: int = TOP) -> list[dict[str, Any]]:
    """The `top` largest folders right under `root`, largest first, in megabytes."""
    try:
        folders = [p for p in root.iterdir() if p.is_dir() and not p.is_symlink()]
    except OSError:
        return []
    sized = sorted(((size_of(p), p.name) for p in folders), reverse=True)[:top]
    return [{"folder": name, "mb": round(size / 1_000_000)} for size, name in sized]


def reading(disk: Path, root: Path) -> dict[str, Any]:
    """How full `disk` is, and the largest folders under `root`."""
    usage = shutil.disk_usage(disk)
    return {
        "percent": round(100 * usage.used / usage.total) if usage.total else 0,
        "used_gb": round(usage.used / 1e9, 1),
        "total_gb": round(usage.total / 1e9, 1),
        "largest": largest(root),
        "at": datetime.now(UTC).isoformat(timespec="seconds"),
    }


def machine_reading() -> dict[str, Any]:
    """This machine's temporary disk, and the largest of the sessions' folders on it."""
    return reading(Path(tempfile.gettempdir()), sessions_root())


def _size(mb: int) -> str:
    return f"{mb / 1000:.1f} GB" if mb >= 1000 else f"{mb} MB"  # noqa: PLR2004 - a gigabyte


def say(now: dict[str, Any], level: int) -> str:
    """One sentence to read out: how full the disk is, and which folders hold most."""
    held = ", ".join(f"{f['folder']} ({_size(f['mb'])})" for f in now["largest"]) or "none named"
    stop = ", and sessions stop when it fills" if level >= FULL else ""
    return f"The shared temporary disk is {now['percent']} % full{stop}; the largest folders are {held}."


class Watch:
    """Measures the disk at most every `every` seconds, and says so once per level it passes."""

    def __init__(
        self, measure: Callable[[], dict[str, Any]], clock: Callable[[], float] = time.monotonic, every: int = EVERY
    ) -> None:
        self.measure, self.clock, self.every = measure, clock, every
        self.last: dict[str, Any] | None = None  # the latest reading, for the health page
        self._due = 0.0
        self._told = 0  # the highest level said since the disk was last below it

    def look(self) -> str | None:
        """A sentence when the disk has just passed a level, else None."""
        now = self.clock()
        # RULE: the temporary disk is measured at most every ten minutes
        if now < self._due:
            return None
        self._due = now + self.every
        try:
            self.last = self.measure()
        except OSError:
            return None  # a disk that can't be read now is read again next time
        percent = self.last["percent"]
        level = FULL if percent >= FULL else WARN if percent >= WARN else 0
        # RULE: a temporary disk that falls back is reported again when it passes a level next
        if level < self._told:
            self._told = level
        # RULE: the temporary disk is reported once each time it passes a level
        if level > self._told:
            self._told = level
            return say(self.last, level)
        return None
