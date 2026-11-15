"""The helper's look at where it runs from, before it does anything.

If any code it could import sat in a home folder, or anywhere a user could write, a Claude
session of that user could edit it and run its own code with the helper's power. So it
refuses to start, and logs why. Run with `-I`, Python adds no current folder or user site.
"""

from __future__ import annotations

import os
import stat
import sys
import sysconfig
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

HELPER_ROOT = "/usr/local/lib/claude-voice-power"


def system_folders() -> tuple[str, ...]:
    """The system Python's own folders: its library, and the library beside its prefix."""
    found = {sysconfig.get_path("stdlib"), sysconfig.get_path("platstdlib", vars={"base": sys.base_prefix})}
    found.add(str(Path(sys.base_prefix) / "lib"))
    return tuple(sorted(os.path.realpath(path) for path in found if path))


def _inside(path: str, folder: str) -> bool:
    return path == folder or path.startswith(folder.rstrip("/") + "/")


def unsafe(path: str, allowed: Iterable[str], status: Callable[[str], Any]) -> str | None:
    """Why `path` may not be imported from, or None. Each folder on the way up must be root's
    and closed to everyone else, or a user could swap what lies below it."""
    if _inside(path, "/home") or _inside(path, "/root"):
        return f"{path} is in a home folder"
    if not any(_inside(path, folder) for folder in allowed):
        return f"{path} is not under {HELPER_ROOT} or the system Python's folders"
    here = path
    while True:
        try:
            info = status(here)
        except FileNotFoundError:
            info = None
        if info is not None and (info.st_uid != 0 or info.st_mode & (stat.S_IWGRP | stat.S_IWOTH)):
            return f"{here} can be written by someone other than root"
        parent = os.path.dirname(here)
        if parent == here:
            return None
        here = parent


def problems(
    paths: Iterable[str],
    allowed: Iterable[str] = (HELPER_ROOT,),
    status: Callable[[str], Any] = os.stat,
    resolve: Callable[[str], str] = os.path.realpath,
) -> list[str]:
    """Every reason the helper must not start: its own file and each import path."""
    found = []
    for entry in paths:
        real = resolve(entry or ".")  # an empty entry means the current folder
        # RULE: the helper refuses to start from a home folder or a folder others can write
        reason = unsafe(real, tuple(allowed), status)
        if reason:
            found.append(reason)
    return found


def check(log: Callable[[str], None]) -> bool:
    """True when the helper may run. Otherwise says why through `log`, and the caller stops."""
    own = [__file__, *sys.path]
    bad = problems(own, (HELPER_ROOT, *system_folders()))
    for reason in bad:
        log(f"refusing to start: {reason}")
    return not bad
