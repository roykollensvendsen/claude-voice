"""The owner's home position, kept once for every plugin that needs it.

``~/.config/claude-voice-home/home.toml`` holds only ``lat`` and ``lon``, rounded to 2
decimals (about 1 km), owner-only in an owner-only folder. It is outside the bridge's own
config and every repository. Only ``plugins install`` (the first time a plugin with
``uses = ["home"]`` needs it) and ``claude-voice home set`` write it, asking in the
terminal without showing the answer. A plugin is given the file's path in its own config,
``home_file``, and reads it itself; the bridge passes it nothing. Nothing here looks the
position up from the network.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import tomli_w

from . import fields, safefile

FOLDER = "claude-voice-home"
SHOWN = "~/.config/claude-voice-home/home.toml"  # as a plugin's home_file names it
NORWAY = {"lat": (57.0, 72.0), "lon": (4.0, 32.0)}
EARTH = {"lat": (-90.0, 90.0), "lon": (-180.0, 180.0)}
ASKED = {
    "lat": "Home latitude (typed or pasted, not shown): ",
    "lon": "Home longitude (not shown): ",
}


def path(home: Path) -> Path:
    """The home file under the owner's home folder, refused through a symlink below
    ~/.config or inside a git repository."""
    return safefile.outside_repositories(safefile.inside(home / ".config", f"{FOLDER}/home.toml"))


def field(axis: str, anywhere: bool) -> fields.Field:
    bounds = (EARTH if anywhere else NORWAY)[axis]
    # RULE: a home position is rounded to two decimals before it is written
    return fields.Field("float", range=bounds, round=2)


def ask(read: Callable[[str], str], say: Callable[[str], None], anywhere: bool) -> dict[str, float]:
    """Latitude and longitude, each asked until it fits, neither shown."""
    return {axis: fields.ask(field(axis, anywhere), ASKED[axis], read, say) for axis in ("lat", "lon")}


def text(position: dict[str, float]) -> str:
    return tomli_w.dumps(position)


def write(home: Path, position: dict[str, float], say: Callable[[str], None]) -> Path:
    """Write the home file, owner-only in an owner-only folder; its path."""
    target = path(home)
    if safefile.private_folder(target.parent):
        say(f"Narrowed ~/.config/{FOLDER} to owner-only (0700); others could list it.")
    safefile.replace(target, text(position))
    return target


def _keep_one(target: Path, spare: Path) -> None:
    """Remove the older backups, so earlier positions do not pile up."""
    for old in target.parent.glob(f"{target.name}.*.bak"):
        # RULE: home set keeps one backup at most
        if old != spare:
            old.unlink()


def set_position(
    home: Path,
    read: Callable[[str], str],
    say: Callable[[str], None],
    anywhere: bool,
    now: datetime | None = None,
) -> None:
    """`claude-voice home set`: ask again, back the old file up, write the new one, and say
    how to undo it, never what was typed."""
    target = path(home)
    position = ask(read, say, anywhere)  # asked before anything is written
    if target.exists():
        spare = safefile.backup(target, now or datetime.now())
        write(home, position, say)
        _keep_one(target, spare)
        say(f"Saved, rounded to 2 decimals: {SHOWN}. Every plugin reads it at its next look.")
        say(f"  undo: cp {safefile.shown(spare, home)} {SHOWN}")
        return
    write(home, position, say)
    say(f"Saved, rounded to 2 decimals: {SHOWN}.")
    say(f"  undo: rm {SHOWN}")
