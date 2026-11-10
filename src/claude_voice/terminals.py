"""Bringing a running session's terminal to the front on this computer (ADR-CV-012).

A session in tmux gets the terminal already attached to it raised, or a new terminal
attached to it. A session in a plain terminal gets the window that holds it raised. A
second copy of a session is never started: nothing here runs `claude`.

On GNOME a plain raise is ignored, so windows are listed and raised through the GNOME
Shell extension computer-use-linux installs. Every gnome-terminal window belongs to one
process, so a window cannot be told apart by its process or its tty; it is found by its
title instead, which Claude Code sets to the session's name after a status sign.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from typing import Any, Protocol

RAISED = (200, {"done": "raised"})
ATTACHED = (200, {"done": "attached"})
NO_WINDOW = (409, {"reason": "no_window"})
NOT_RUNNING = (409, {"reason": "not_running"})
NOT_LOCAL = (409, {"reason": "not_local"})
NO_WINDOW_LIST = (501, {"reason": "no_window_list"})

TERMINAL_CLASSES = ("terminal", "ptyxis", "kgx", "konsole", "kitty", "alacritty", "wezterm", "ghostty", "xterm")
NEW_TERMINAL = ["gnome-terminal", "--"]

Answer = tuple[int, dict[str, str]]


class NoWindowList(Exception):
    """This desktop cannot list or raise windows."""


class Windows(Protocol):
    def list(self) -> list[dict[str, Any]]: ...

    def activate(self, window_id: int) -> bool: ...


def _run(command: list[str]) -> str:
    return subprocess.run(command, check=True, capture_output=True, text=True, timeout=5).stdout


def _spawn(command: list[str]) -> None:
    """Start a program that outlives the bridge and is not tied to its terminal."""
    subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


class ShellWindows:
    """The window list and window raising of the computer-use-linux GNOME Shell extension."""

    BUS = "dev.avifenesh.ComputerUseLinux.WindowControl"
    PATH = "/dev/avifenesh/ComputerUseLinux/WindowControl"

    def __init__(self, run: Callable[[list[str]], str] = _run) -> None:
        self.run = run

    def _call(self, method: str, *args: str) -> list[Any]:
        command = ["busctl", "--user", "--json=short", "call", self.BUS, self.PATH, self.BUS, method, *args]
        return list(json.loads(self.run(command))["data"])

    def list(self) -> list[dict[str, Any]]:
        try:
            windows = json.loads(self._call("ListWindows")[0])
        except (OSError, subprocess.SubprocessError, ValueError, KeyError, IndexError) as exc:
            raise NoWindowList(str(exc)) from exc
        return [w for w in windows if isinstance(w, dict)]

    def activate(self, window_id: int) -> bool:
        try:
            return bool(self._call("ActivateWindow", "t", str(int(window_id)))[0])
        except (OSError, subprocess.SubprocessError, ValueError, KeyError, IndexError):
            return False


def title_name(title: str) -> str:
    """The session name in a terminal title Claude Code set: its status sign taken off."""
    head, _, rest = title.partition(" ")
    return rest if rest and head and not any(ch.isalnum() for ch in head) else title


def _is_terminal(window: dict[str, Any]) -> bool:
    kind = f"{window.get('wm_class') or ''} {window.get('app_id') or ''}".lower()
    return any(name in kind for name in TERMINAL_CLASSES)


class Terminals:
    def __init__(
        self,
        windows: Windows | None = None,
        run: Callable[[list[str]], str] = _run,
        spawn: Callable[[list[str]], None] = _spawn,
    ) -> None:
        self.windows = windows or ShellWindows()
        self.run, self.spawn = run, spawn

    def _window_of(self, names: list[str]) -> int | None:
        """The one terminal window titled with one of `names`; None when there is none or
        more than one. Raises NoWindowList."""
        titled = [w for w in self.windows.list() if _is_terminal(w)]
        # RULE: only the terminal titled with the session name is raised
        found = [w for w in titled if title_name(str(w.get("title") or "")) in names]
        return int(found[0]["window_id"]) if len(found) == 1 else None

    def _raise(self, names: list[str]) -> bool:
        window = self._window_of([n for n in names if n])
        return window is not None and self.windows.activate(window)

    def _attached(self, tmux_session: str) -> bool:
        try:
            out = self.run(["tmux", "list-clients", "-t", f"={tmux_session}", "-F", "#{client_tty}"])
        except (OSError, subprocess.SubprocessError):
            return False
        return bool(out.strip())

    def _open_tmux(self, names: list[str], tmux_session: str) -> Answer:
        # RULE: a terminal already attached to a tmux session is raised, not opened again
        if self._attached(tmux_session):
            try:
                if self._raise([*names, tmux_session]):
                    return RAISED
            except NoWindowList:
                pass  # a new terminal can still be opened
        try:
            # A new terminal joins the running session; `claude` is never run again.
            self.spawn([*NEW_TERMINAL, "tmux", "attach-session", "-t", f"={tmux_session}"])
        except OSError:
            return NO_WINDOW_LIST
        return ATTACHED

    def focus(self, live: dict[str, Any], pane: tuple[str, str] | None) -> Answer:
        """Bring running session `live` (its Claude Code record) to the front. `pane` is
        (tmux session, pane) when it runs in tmux."""
        if live.get("kind") != "interactive":
            return NO_WINDOW  # a background session has no terminal
        # Claude Code titles the terminal with its own name, which a rename here may not have reached.
        names = [str(live.get(key) or "") for key in ("deliver_to", "name")]
        if pane is not None:
            return self._open_tmux(names, pane[0])
        try:
            # A plain terminal whose window is not found is not opened again: it may be anywhere.
            return RAISED if self._raise(names) else NO_WINDOW
        except NoWindowList:
            return NO_WINDOW_LIST

    def open(
        self,
        session_id: str,
        live: list[dict[str, Any]],
        pane_of: Callable[[int], tuple[str, str] | None],
    ) -> Answer:
        """Bring session `session_id` here to the front, found among the running sessions."""
        found = next((d for d in live if str(d.get("sessionId")) == session_id), None)
        if found is None:
            return NOT_RUNNING
        if found.get("kind") != "interactive":
            return NO_WINDOW  # a background session has no terminal
        return self.focus(found, pane_of(int(found.get("pid") or 0)))
