"""Bringing a session's terminal to the front (ADR-CV-012), with fakes for the desktop:
nothing here opens or raises a real window."""

import json
import subprocess

import pytest

from claude_voice import terminals
from claude_voice.terminals import RAISED, NoWindowList, ShellWindows, Terminals


class Windows:
    """Stands in for the GNOME Shell window list."""

    def __init__(self, windows=None, missing=False, refuses=False):
        self.windows = windows or []
        self.missing, self.refuses = missing, refuses
        self.raised = []

    def list(self):
        if self.missing:
            raise NoWindowList("no extension")
        return self.windows

    def activate(self, window_id):
        self.raised.append(window_id)
        return not self.refuses


class Tmux:
    """Stands in for the tmux command: which terminals are attached to which session."""

    def __init__(self, attached=None):
        self.attached = attached or {}
        self.asked = []

    def __call__(self, command):
        self.asked.append(command)
        assert command[:2] == ["tmux", "list-clients"]
        session = command[command.index("-t") + 1].lstrip("=")
        return "".join(f"{tty}\n" for tty in self.attached.get(session, []))


class Spawned(list):
    def __call__(self, command):
        self.append(command)


def term(window_id, title, wm_class="org.gnome.Terminal"):
    return {"window_id": window_id, "title": title, "wm_class": wm_class, "pid": 6565}


def desk(windows=None, attached=None):
    windows = windows if windows is not None else Windows()
    spawned = Spawned()
    return Terminals(windows, run=Tmux(attached), spawn=spawned), windows, spawned


LIVE = {"sessionId": "s-1", "name": "garage-door", "kind": "interactive", "pid": 41}


def test_only_the_terminal_titled_with_the_session_name_is_raised():
    t, windows, spawned = desk(Windows([term(1, "◐ tv-mcp-work"), term(2, "✳ garage-door")]))
    assert t.focus(LIVE, pane=None) == (200, {"done": "raised"})
    assert windows.raised == [2] and spawned == []


def test_a_window_of_another_program_with_the_same_title_is_not_raised():
    t, windows, _ = desk(Windows([term(5, "garage-door", wm_class="firefox")]))
    assert t.focus(LIVE, pane=None) == (409, {"reason": "no_window"})
    assert windows.raised == []


def test_a_plain_terminal_session_whose_window_is_not_found_is_not_started_again():
    t, windows, spawned = desk(Windows([term(1, "◐ tv-mcp-work")]))
    assert t.focus(LIVE, pane=None) == (409, {"reason": "no_window"})
    assert windows.raised == [] and spawned == []


def test_two_windows_with_the_name_are_too_unsure_to_raise_either():
    t, windows, _ = desk(Windows([term(1, "✳ garage-door"), term(2, "✳ garage-door")]))
    assert t.focus(LIVE, pane=None) == (409, {"reason": "no_window"})
    assert windows.raised == []


def test_a_window_the_desktop_will_not_raise_is_no_window():
    t, _, _ = desk(Windows([term(2, "✳ garage-door")], refuses=True))
    assert t.focus(LIVE, pane=None) == (409, {"reason": "no_window"})


def test_without_a_window_list_a_plain_terminal_session_cannot_be_raised():
    t, _, spawned = desk(Windows(missing=True))
    assert t.focus(LIVE, pane=None) == (501, {"reason": "no_window_list"})
    assert spawned == []


def test_a_terminal_already_attached_to_a_tmux_session_is_raised_not_opened_again():
    t, windows, spawned = desk(Windows([term(7, "✳ garage-door")]), attached={"garage": ["/dev/pts/3"]})
    assert t.focus(LIVE, pane=("garage", "%4")) == (200, {"done": "raised"})
    assert windows.raised == [7] and spawned == []


def test_a_tmux_session_with_no_terminal_attached_opens_one_attached_to_it():
    t, windows, spawned = desk(Windows([]), attached={})
    assert t.focus(LIVE, pane=("garage", "%4")) == (200, {"done": "attached"})
    assert spawned == [["gnome-terminal", "--", "tmux", "attach-session", "-t", "=garage"]]
    assert windows.raised == []


def test_a_tmux_session_whose_attached_terminal_is_not_found_gets_a_terminal_of_its_own():
    t, _, spawned = desk(Windows([term(1, "roy@laptop:~")]), attached={"garage": ["/dev/pts/3"]})
    assert t.focus(LIVE, pane=("garage", "%4")) == (200, {"done": "attached"})
    assert spawned == [["gnome-terminal", "--", "tmux", "attach-session", "-t", "=garage"]]


def test_a_tmux_session_gets_a_terminal_even_without_a_window_list():
    t, _, spawned = desk(Windows(missing=True), attached={"garage": ["/dev/pts/3"]})
    assert t.focus(LIVE, pane=("garage", "%4")) == (200, {"done": "attached"})
    assert len(spawned) == 1


def test_a_session_that_is_not_a_terminal_has_no_window():
    t, windows, spawned = desk(Windows([term(2, "✳ garage-door")]))
    assert t.focus({**LIVE, "kind": "bg"}, pane=None) == (409, {"reason": "no_window"})
    assert windows.raised == [] and spawned == []


def test_without_a_terminal_program_a_tmux_session_cannot_be_opened():
    def missing(command):
        raise FileNotFoundError(command[0])

    t = Terminals(Windows([]), run=Tmux(), spawn=missing)
    assert t.focus(LIVE, pane=("garage", "%4")) == (501, {"reason": "no_window_list"})


def test_a_renamed_session_is_found_by_the_name_claude_code_still_has():
    t, windows, _ = desk(Windows([term(3, "✳ garage-door")]))
    assert t.focus({**LIVE, "name": "garage", "deliver_to": "garage-door"}, pane=None) == RAISED
    assert windows.raised == [3]


def test_an_attached_tmux_terminal_is_also_found_by_the_tmux_session_name():
    t, windows, spawned = desk(Windows([term(9, "garage")]), attached={"garage": ["/dev/pts/3"]})
    assert t.focus(LIVE, pane=("garage", "%4")) == RAISED
    assert windows.raised == [9] and spawned == []


def test_a_session_is_found_among_the_running_ones_by_its_id():
    t, windows, _ = desk(Windows([term(2, "✳ garage-door")]))
    panes = []

    def pane_of(pid):
        panes.append(pid)

    assert t.open("s-1", [{**LIVE, "sessionId": "other"}, LIVE], pane_of) == RAISED
    assert panes == [41] and windows.raised == [2]


def test_a_session_that_is_not_running_is_not_started():
    t, windows, spawned = desk(Windows([term(2, "✳ garage-door")]))
    assert t.open("gone", [LIVE], lambda pid: None) == (409, {"reason": "not_running"})
    assert windows.raised == [] and spawned == []


def test_a_session_name_is_read_out_of_the_title_claude_code_gives_its_terminal():
    assert terminals.title_name("✳ garage-door") == "garage-door"
    assert terminals.title_name("◐ claude-voice-work") == "claude-voice-work"
    assert terminals.title_name("garage-door") == "garage-door"
    assert terminals.title_name("roy@laptop: ~") == "roy@laptop: ~"


# -- the GNOME Shell extension, through busctl ------------------------------------------


def test_the_window_list_is_read_from_the_shell_extension():
    asked = []

    def run(command):
        asked.append(command)
        return json.dumps({"type": "s", "data": [json.dumps([term(1, "✳ garage-door")])]})

    assert ShellWindows(run).list() == [term(1, "✳ garage-door")]
    assert asked[0][:4] == ["busctl", "--user", "--json=short", "call"]
    assert asked[0][-1] == "ListWindows"


@pytest.mark.parametrize("failure", [FileNotFoundError("busctl"), subprocess.CalledProcessError(1, "busctl")])
def test_a_missing_shell_extension_means_no_window_list(failure):
    def run(command):
        raise failure

    with pytest.raises(NoWindowList):
        ShellWindows(run).list()


def test_an_unreadable_answer_from_the_shell_extension_means_no_window_list():
    with pytest.raises(NoWindowList):
        ShellWindows(lambda command: "not json").list()


def test_raising_a_window_asks_the_shell_extension_by_its_id():
    asked = []

    def run(command):
        asked.append(command)
        return json.dumps({"type": "bs", "data": [True, "ok"]})

    assert ShellWindows(run).activate(833193622) is True
    assert asked[0][-3:] == ["ActivateWindow", "t", "833193622"]


def test_a_window_the_shell_extension_cannot_raise_is_reported_as_not_raised():
    assert ShellWindows(lambda c: json.dumps({"type": "bs", "data": [False, "gone"]})).activate(1) is False

    def broken(command):
        raise subprocess.CalledProcessError(1, "busctl")

    assert ShellWindows(broken).activate(1) is False
