"""The one home position every plugin that needs it reads: ~/.config/claude-voice-home/home.toml."""

import getpass
import stat
import sys
import tomllib

import pytest

from claude_voice import cli, home

# Owner-only file modes, symlinks and owner ids are what these tests check, and Windows has none
# of them as POSIX does. The bridge that installs plugins runs on Linux.
pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes and symlinks")


def typed(*answers):
    """Answers for getpass, in order, and the prompts it was given."""
    given, prompts = iter(answers), []

    def read(prompt=""):
        prompts.append(prompt)
        return next(given)

    read.prompts = prompts
    return read


def env(tmp_path):
    return {"HOME": str(tmp_path)}


def test_a_home_position_is_rounded_to_two_decimals_before_it_is_written(tmp_path):
    said = []
    home.set_position(tmp_path, typed("59,91234", "10.7489"), said.append, anywhere=False)
    text = (tmp_path / ".config/claude-voice-home/home.toml").read_text()
    assert tomllib.loads(text) == {"lat": 59.91, "lon": 10.75}
    assert "9123" not in text and "748" not in text


def test_the_home_file_is_readable_by_its_owner_alone(tmp_path):
    home.set_position(tmp_path, typed("59.9", "10.7"), lambda line: None, anywhere=False)
    folder = tmp_path / ".config/claude-voice-home"
    assert stat.S_IMODE(folder.stat().st_mode) == 0o700
    assert stat.S_IMODE((folder / "home.toml").stat().st_mode) == 0o600


def test_a_home_outside_norway_is_asked_again_unless_anywhere_is_given(tmp_path):
    said = []
    read = typed("48.85", "59.9", "2.35", "10.7")
    home.set_position(tmp_path, read, said.append, anywhere=False)
    assert len(read.prompts) == 4 and sum("from 57 to 72" in s for s in said) == 1
    home.set_position(tmp_path, typed("48.85", "2.35"), said.append, anywhere=True)
    assert tomllib.loads((tmp_path / ".config/claude-voice-home/home.toml").read_text()) == {"lat": 48.85, "lon": 2.35}


def test_home_set_refuses_outside_a_terminal_before_asking_anything(tmp_path, monkeypatch, capsys):
    def never(prompt=""):
        raise AssertionError("asked a question outside a terminal")

    monkeypatch.setattr(getpass, "getpass", never)
    assert cli.main(["home", "set"], env=env(tmp_path)) == 1
    assert "home set asks you questions; run it in a terminal of your own" in capsys.readouterr().err
    assert not (tmp_path / ".config").exists()


def test_home_set_shows_no_value_and_says_how_to_undo_a_change(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "_terminal", lambda env: True)
    monkeypatch.setattr(getpass, "getpass", typed("59.91", "10.75", "60.39", "5.32"))
    assert cli.main(["home", "set"], env=env(tmp_path)) == 0
    first = capsys.readouterr().out
    assert "rm ~/.config/claude-voice-home/home.toml" in first
    assert cli.main(["home", "set"], env=env(tmp_path)) == 0
    second = capsys.readouterr().out
    [backup] = (tmp_path / ".config/claude-voice-home").glob("home.toml.*.bak")
    assert f"cp ~/.config/claude-voice-home/{backup.name} ~/.config/claude-voice-home/home.toml" in second
    assert tomllib.loads(backup.read_text()) == {"lat": 59.91, "lon": 10.75}
    for value in ("59.91", "10.75", "60.39", "5.32", "59,91"):
        assert value not in first + second


@pytest.mark.parametrize("stop", [EOFError, KeyboardInterrupt])
def test_home_set_stopped_halfway_writes_nothing(tmp_path, monkeypatch, capsys, stop):
    monkeypatch.setattr(cli, "_terminal", lambda env: True)
    answers = iter(["59.91"])

    def read(prompt=""):
        try:
            return next(answers)
        except StopIteration:
            raise stop from None

    monkeypatch.setattr(getpass, "getpass", read)
    assert cli.main(["home", "set"], env=env(tmp_path)) == 1
    assert "nothing was changed" in capsys.readouterr().out.lower()
    assert not (tmp_path / ".config/claude-voice-home/home.toml").exists()


def test_a_private_file_inside_a_git_repository_is_refused(tmp_path, monkeypatch, capsys):
    """~/.config is often a link into a dotfiles repository, and a commit there would
    publish the position."""
    dotfiles = tmp_path / "dotfiles"
    (dotfiles / ".git").mkdir(parents=True)
    (dotfiles / "config").mkdir()
    (tmp_path / "home").mkdir()
    (tmp_path / "home/.config").symlink_to(dotfiles / "config")

    def never(prompt=""):
        raise AssertionError("asked for a position that could not be kept safely")

    monkeypatch.setattr(cli, "_terminal", lambda env: True)
    monkeypatch.setattr(getpass, "getpass", never)
    assert cli.main(["home", "set"], env=env(tmp_path / "home")) == 1
    err = capsys.readouterr().err
    assert str(dotfiles) in err and "git repository" in err
    assert list((dotfiles / "config").iterdir()) == []


def test_an_owner_only_folder_that_is_wider_is_narrowed(tmp_path):
    folder = tmp_path / ".config/claude-voice-home"
    folder.mkdir(parents=True)
    folder.chmod(0o755)
    said = []
    home.set_position(tmp_path, typed("59.9", "10.7"), said.append, anywhere=False)
    assert stat.S_IMODE(folder.stat().st_mode) == 0o700
    assert any("owner-only" in line and "~/.config/claude-voice-home" in line for line in said)


def test_home_set_keeps_one_backup_at_most(tmp_path):
    said = []
    for lat in ("59.91", "60.39", "63.43"):
        home.set_position(tmp_path, typed(lat, "10.75"), said.append, anywhere=False)
    [backup] = (tmp_path / ".config/claude-voice-home").glob("home.toml.*.bak")
    assert tomllib.loads(backup.read_text())["lat"] == 60.39  # the one the last undo line names
    assert backup.name in said[-1]
