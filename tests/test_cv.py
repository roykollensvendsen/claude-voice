"""cv: the sessions file read from a terminal, the same locally and over SSH."""

import json
import os
import subprocess
import tomllib
from pathlib import Path

import pytest

from claude_voice import cv, roster

NOW = "2026-11-16T10:00+00:00"
PYPROJECT = Path(__file__).parent.parent / "pyproject.toml"


def row(organisation, role, conversation, tmux="", folder="/work"):
    return roster.Row(organisation, role, folder, conversation, tmux, NOW)


@pytest.fixture
def home(tmp_path):
    (tmp_path / ".claude" / "sessions").mkdir(parents=True)
    return tmp_path


def keep(home, *rows):
    roster.write(home / ".local" / "state" / "claude-voice" / "sessions.toml", rows)


def running(home, conversation, status="idle", tmux=None):
    """A running session's record, as Claude Code writes it; this test's own process keeps it alive."""
    record = {"pid": os.getpid(), "sessionId": conversation, "status": status, "tmux": tmux, "kind": "interactive"}
    (home / ".claude" / "sessions" / f"{conversation}.json").write_text(json.dumps(record))


class Tmux:
    """tmux as seen by cv: each command is noted, and a pane shows `screen`."""

    def __init__(self, screen="", fails=False):
        self.screen, self.fails, self.ran = screen, fails, []

    def __call__(self, command):
        self.ran.append(command)
        if self.fails:
            raise subprocess.CalledProcessError(1, command, stderr="can't find pane: %9")
        return self.screen


def cv_main(home, *args, tmux=None):
    return cv.main(list(args), env={"HOME": str(home)}, run=tmux or Tmux())


def test_cv_is_installed_as_a_command_of_its_own():
    scripts = tomllib.loads(PYPROJECT.read_text())["project"]["scripts"]
    assert scripts["cv"] == "claude_voice.cv:entry"


def test_cv_ls_says_each_role_s_state(home, capsys):
    keep(
        home,
        row("voice", "manager", "a"),
        row("voice", "analyst", "b"),
        row("lab", "lead", "c"),
        row("solo", "x", "d"),
    )
    running(home, "a", "busy")
    running(home, "b", "idle")
    running(home, "c", "waiting")
    assert cv_main(home, "ls") == 0
    assert capsys.readouterr().out.splitlines() == [
        "lab   lead    needs you",
        "solo  x       gone",
        "voice analyst idle",
        "voice manager busy",
    ]


def test_a_cv_ls_line_fits_a_phone_in_40_characters(home, capsys):
    keep(home, row("a-very-long-organisation", "claude-voice-mobile-architect-for-the-phone", "a"))
    cv_main(home, "ls")
    line = capsys.readouterr().out.splitlines()[0]
    assert len(line) < 40 and line.endswith(" gone")
    assert line.startswith("a-very-lo… claude-voice-mobile")


def test_cv_ls_with_no_sessions_file_says_the_bridge_writes_it(home, capsys):
    assert cv_main(home, "ls") == 1
    err = capsys.readouterr().err
    assert "sessions.toml" in err and "systemctl --user status claude-voice" in err


def test_cv_ls_with_an_unreadable_file_says_to_remove_it(home, capsys):
    path = home / ".local" / "state" / "claude-voice" / "sessions.toml"
    path.parent.mkdir(parents=True)
    path.write_text("not = [toml")
    assert cv_main(home, "ls") == 1
    assert "remove it" in capsys.readouterr().err


def test_cv_peek_shows_the_last_20_lines_of_the_role_s_pane(home, capsys):
    keep(home, row("voice", "manager", "a", tmux="voice:@3.%3"))
    running(home, "a", tmux="voice:@4.%4")  # the pane Claude Code names now wins over the file's
    tmux = Tmux("\n".join(f"line {n}" for n in range(30)) + "\n\n\n")
    assert cv_main(home, "peek", "manager", tmux=tmux) == 0
    assert capsys.readouterr().out.splitlines() == [f"line {n}" for n in range(10, 30)]
    assert tmux.ran == [["tmux", "capture-pane", "-p", "-J", "-t", "%4"]]


def test_cv_peek_takes_an_organisation_and_a_role(home, capsys):
    keep(home, row("voice", "manager", "a", tmux="voice:@3.%3"), row("lab", "manager", "b", tmux="lab:@5.%5"))
    running(home, "a", tmux="voice:@3.%3")
    running(home, "b", tmux="lab:@5.%5")
    tmux = Tmux("hello\n")
    assert cv_main(home, "peek", "lab/manager", tmux=tmux) == 0
    assert tmux.ran[0][-1] == "%5"


def test_a_role_in_two_organisations_is_asked_for_by_its_full_name(home, capsys):
    keep(home, row("voice", "manager", "a"), row("lab", "manager", "b"))
    tmux = Tmux()
    assert cv_main(home, "peek", "manager", tmux=tmux) == 1
    err = capsys.readouterr().err
    assert "lab/manager" in err and "voice/manager" in err and tmux.ran == []


def test_cv_peek_of_an_unknown_role_points_at_cv_ls(home, capsys):
    keep(home, row("voice", "manager", "a"))
    assert cv_main(home, "peek", "nobody") == 1
    assert "cv ls" in capsys.readouterr().err


def test_cv_peek_of_a_gone_role_says_how_to_resume_it(home, capsys):
    keep(home, row("voice", "manager", "conv-1", folder="/work/voice"))
    tmux = Tmux()
    assert cv_main(home, "peek", "manager", tmux=tmux) == 1
    err = capsys.readouterr().err
    assert "not running" in err and "cd /work/voice && claude --resume conv-1" in err and tmux.ran == []


def test_cv_peek_of_a_role_outside_tmux_says_where_to_look(home, capsys):
    keep(home, row("voice", "manager", "a"))
    running(home, "a", tmux=None)
    assert cv_main(home, "peek", "manager") == 1
    assert "not in tmux" in capsys.readouterr().err


def test_cv_peek_says_what_tmux_said_when_it_cannot_show_the_pane(home, capsys):
    keep(home, row("voice", "manager", "a"))
    running(home, "a", tmux="voice:@9.%9")
    assert cv_main(home, "peek", "manager", tmux=Tmux(fails=True)) == 1
    err = capsys.readouterr().err
    assert "can't find pane" in err and "cv ls" in err
