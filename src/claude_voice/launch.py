"""How a session the bridge starts is started: in a window or in the background, with Remote Control or not.

The defaults come from the settings file's ``[sessions]`` section; the companion app's
settings page changes them for every new session, and one start can ask otherwise.
Which terminal opens is the settings file's ``terminal_command`` (with ``{cwd}`` for the
folder), or the first known one found; without a desktop it is a tmux session that can be
attached, and with no terminal at all the session starts in the background.
"""

from __future__ import annotations

import shlex
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any

from .config import LAUNCHES, Sessions

# The terminals tried in order, each with how it is told the folder and the command.
TERMINALS = (
    ("gnome-terminal", ["gnome-terminal", "--working-directory={cwd}", "--"]),
    ("kgx", ["kgx", "--working-directory={cwd}", "--"]),
    ("konsole", ["konsole", "--workdir", "{cwd}", "-e"]),
    ("xterm", ["xterm", "-e"]),
)
TMUX = ["tmux", "new-session", "-d", "-s", "{name}", "-c", "{cwd}"]
SETTING = "sessions"


def terminal_for(
    cwd: str,
    name: str,
    configured: str | None,
    find: Callable[[str], str | None],
    env: Mapping[str, str],
) -> tuple[list[str] | None, str | None]:
    """The command that opens a terminal running what follows it, and where that is
    ("window" or "tmux"); (None, None) when nothing can open one."""
    fill = {"cwd": cwd, "name": name}
    desktop = bool(env.get("WAYLAND_DISPLAY") or env.get("DISPLAY"))
    if configured:
        return [part.format(**fill) for part in shlex.split(configured)], "window"
    if desktop:
        for program, template in TERMINALS:
            if find(program):
                return [part.format(**fill) for part in template], "window"
    if find("tmux"):
        return [part.format(**fill) for part in TMUX], "tmux"
    return None, None


class Settings:
    """How new sessions start: the settings file's defaults, then what the settings page chose."""

    def __init__(self, store: Any, defaults: Sessions) -> None:
        self.store, self.defaults = store, defaults

    def get(self) -> dict[str, Any]:
        chosen = self.store.get_setting(SETTING) or {}
        return {
            "remote_control": chosen.get("remote_control", self.defaults.remote_control),
            "start_in": chosen.get("start_in", self.defaults.launch),
            "updated_at": chosen.get("updated_at"),
        }

    def change(self, body: Any) -> dict[str, Any]:
        """Apply a partial change; ValueError says what does not fit."""
        if not isinstance(body, dict) or not body:
            raise ValueError("send an object with remote_control and/or start_in")
        unknown = sorted(set(body) - {"remote_control", "start_in"})
        if unknown:
            raise ValueError(f"no such setting: {', '.join(unknown)}")
        if "remote_control" in body and not isinstance(body["remote_control"], bool):
            raise ValueError("remote_control is true or false")
        if "start_in" in body and body["start_in"] not in LAUNCHES:
            raise ValueError(f"start_in is one of: {', '.join(LAUNCHES)}")
        chosen = {
            **(self.store.get_setting(SETTING) or {}),
            **body,
            "updated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        }
        self.store.set_setting(SETTING, chosen)
        return self.get()


def check(defaults: Sessions, find: Callable[[str], str | None], env: Mapping[str, str]) -> dict[str, Any]:
    """For the doctor: can a new session open where it is meant to."""
    from .doctor import check as make

    terminal, where = terminal_for("~", "check", defaults.terminal_command, find, env)
    wanted = defaults.launch == "terminal"
    ok = not wanted or where == "window"
    if where == "window":
        detail = f"New sessions open in a window: {terminal[0] if terminal else ''}"
    elif where == "tmux":
        detail = "No desktop is reachable, so new sessions open in tmux (attach with `tmux attach`)"
    else:
        detail = "No terminal was found, so new sessions start in the background"
    return make(
        "terminal",
        "A terminal for new sessions",
        ok,
        detail,
        affects="seeing a session the bridge starts on the desktop",
        fix='Install a terminal, or name yours in [sessions] terminal_command, such as "kitty --directory {cwd}"',
    )
