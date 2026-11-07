"""Renaming a running session: the name the bridge shows, the old names that still reach it,
and, where the session runs in tmux and is idle, `/rename` typed into Claude Code itself.

Claude Code keeps a session's name in its own record. The bridge cannot change that record,
only ask Claude Code to by typing `/rename`, and only where a terminal it can type into
exists. So the bridge keeps its own name on top: shown while Claude Code still has the
old one, dropped once Claude Code has the new one or someone renames it another way.
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Callable, Iterable
from typing import Any

NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")


def valid(name: str) -> bool:
    # RULE: a session name is letters, digits, dots, underscores and dashes
    return NAME.fullmatch(name) is not None


def apply(rows: Iterable[dict[str, Any]], names: dict[str, tuple[str, str]], aliases: dict[str, list[str]]):
    """The running sessions with the bridge's names on top. `names` maps a session id to
    (the name Claude Code had when it was renamed, the new name); `aliases` its old names.
    `deliver_to` is the name Claude Code itself still answers to, when it differs."""
    out = []
    for d in rows:
        sid = str(d.get("sessionId"))
        d = {**d, "aliases": aliases.get(sid, [])}
        was, new = names.get(sid, (None, None))
        if new and d.get("name") == was and was != new:
            d["deliver_to"], d["name"] = d.get("name"), new
        out.append(d)
    return out


def called(d: dict[str, Any], session: str, old_names: bool = False) -> bool:
    """Whether `session` names this running session: by name or id, or with `old_names`
    by a name it had before."""
    if session in (d.get("name"), str(d.get("sessionId"))):
        return True
    # RULE: a message to an old name reaches the renamed session
    return old_names and (session in d.get("aliases", []) or session == d.get("deliver_to"))


def find(rows: list[dict[str, Any]], session: str) -> dict[str, Any] | None:
    """The running session `session` names: its own name first, then a name it had."""
    for old_names in (False, True):
        for d in rows:
            if called(d, session, old_names):
                return d
    return None


def taken(rows: Iterable[dict[str, Any]], but: str | None = None) -> set[str]:
    """Every name a running session answers to, other than session `but`'s."""
    out: set[str] = set()
    for d in rows:
        if str(d.get("sessionId")) != but:
            out |= {str(d.get("name")), *d.get("aliases", []), *([d["deliver_to"]] if "deliver_to" in d else [])}
    return out


class Tmux:
    """Typing into a tmux pane, found by the process running in it."""

    def __init__(self, run: Callable[[list[str]], str] | None = None) -> None:
        self.run = run or self._run

    @staticmethod
    def _run(command: list[str]) -> str:
        return subprocess.run(command, check=True, capture_output=True, text=True, timeout=5).stdout

    def pane(self, pid: int) -> tuple[str, str] | None:
        """(tmux session, pane) where process `pid` runs, if it runs directly in a pane."""
        try:
            listing = self.run(["tmux", "list-panes", "-a", "-F", "#{pane_pid} #{session_name} #{pane_id}"])
        except (OSError, subprocess.SubprocessError):
            return None
        for line in listing.splitlines():
            parts = line.split(" ")
            if len(parts) == 3 and parts[0] == str(pid):
                return parts[1], parts[2]
        return None

    def type(self, pane: str, text: str) -> None:
        self.run(["tmux", "send-keys", "-t", pane, "-l", text])
        self.run(["tmux", "send-keys", "-t", pane, "Enter"])

    def rename(self, old: str, new: str) -> None:
        self.run(["tmux", "rename-session", "-t", f"={old}", new])
