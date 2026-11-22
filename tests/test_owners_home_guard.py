"""No test writes under the owner's real home: the bridge's state folder, the owner's settings and
Claude Code files. A test once called the bridge's start under a systemd session and rewrote the
bridge's restart record in the owner's state folder. The suite now refuses such a write, and fails
the test.
"""

import os
import sqlite3
import tempfile
from pathlib import Path

import pytest
from conftest import REAL_HOME, WroteToTheOwnersHome

from claude_voice import config, restarts

CHECKOUT = Path(__file__).resolve().parents[1]
# Inside a folder that does not exist, so a write that got past the guard would fail, not land.
PROBE = REAL_HOME / ".claude-voice-guard-probe-that-never-exists" / "probe"
NOWHERE = CHECKOUT / ".claude-voice-guard-probe-that-never-exists"  # in the checkout, a source that is not there


@pytest.mark.parametrize(
    "write",
    [
        lambda: PROBE.write_text("x", encoding="utf-8"),
        lambda: open(PROBE, "ab"),  # noqa: SIM115
        lambda: os.open(PROBE, os.O_WRONLY | os.O_CREAT),
        lambda: PROBE.unlink(),
        lambda: PROBE.mkdir(),
        lambda: os.replace(PROBE, PROBE.with_name("other")),
        lambda: os.replace(NOWHERE, PROBE),
        lambda: os.symlink(NOWHERE, PROBE),
        lambda: sqlite3.connect(PROBE),
        lambda: os.utime(PROBE),
        pytest.param(
            lambda: os.chown(PROBE, -1, -1),
            marks=pytest.mark.skipif(not hasattr(os, "chown"), reason="Windows has no chown"),
        ),
    ],
)
def test_a_test_that_writes_under_the_owners_home_is_refused_and_fails(no_writes_to_the_owners_home, write):
    with pytest.raises(WroteToTheOwnersHome):
        write()
    assert no_writes_to_the_owners_home  # noted, so the test fails even had it swallowed the refusal
    no_writes_to_the_owners_home.clear()


@pytest.mark.parametrize("name", [".claude/settings.local.json", ".claude/org.toml", "CLAUDE.local.md"])
def test_the_owners_own_files_in_the_checkout_are_never_written(no_writes_to_the_owners_home, name):
    with pytest.raises(WroteToTheOwnersHome):
        os.replace(NOWHERE, CHECKOUT / name)  # had it got past, the missing source fails it
    assert no_writes_to_the_owners_home
    no_writes_to_the_owners_home.clear()


def test_reading_the_owners_home_and_writing_in_the_checkout_or_temp_still_work(tmp_path):
    assert Path(__file__).read_text(encoding="utf-8")  # the checkout lives under the home here
    (tmp_path / "f").write_text("x")


def test_python_may_write_its_own_compiled_files_where_it_is_installed():
    import sys

    from conftest import _owners

    cached = Path(sys.base_prefix) / "lib" / "__pycache__" / "pty.cpython-312.pyc.1"
    assert _owners(cached) is None


def test_a_python_installed_in_the_owners_home_opens_only_its_compiled_files(monkeypatch):
    """A Python built with --prefix=~/.local must not open ~/.local/state with it."""
    import conftest

    monkeypatch.setattr(conftest, "PYTHON", (REAL_HOME / ".local",))
    assert conftest._owners(REAL_HOME / ".local" / "lib" / "__pycache__" / "x.pyc") is None
    assert conftest._owners(REAL_HOME / ".local" / "lib" / "__pycache__" / "x.pyc.140") is None
    assert conftest._owners(REAL_HOME / ".local" / "lib" / "http" / "__pycache__") is None
    state = REAL_HOME / ".local" / "state" / "claude-voice"
    for name in ("restarts", "__pycache__/restarts.json", "__pycache__x/x.pyc"):
        assert conftest._owners(state / name) is not None, name


def test_a_name_removed_inside_an_open_folder_is_read_against_that_folder(tmp_path, monkeypatch):
    """shutil.rmtree removes by name inside the folder it opened, not the one the test runs in."""
    import shutil

    (tmp_path / "tree").mkdir()
    (tmp_path / "tree" / "CLAUDE.local.md").write_text("a worktree's own notes\n")
    monkeypatch.chdir(CHECKOUT)
    shutil.rmtree(tmp_path / "tree")
    assert not (tmp_path / "tree").exists()


def test_a_link_pointing_into_the_owners_home_may_be_made_elsewhere(tmp_path):
    os.symlink(PROBE, tmp_path / "link")  # only the link is written, not what it points at
    assert (tmp_path / "link").is_symlink()


def test_a_process_a_test_starts_has_a_home_of_its_own():
    """The hook cannot see another process, so the home it is handed must not be the owner's."""
    import subprocess
    import sys

    code = "import os, pathlib; print(pathlib.Path.home()); print(os.environ.get('XDG_STATE_HOME', ''))"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout
    temp = Path(tempfile.gettempdir()).resolve()  # on Windows it lies under the home
    for line in out.splitlines():
        assert not Path(line).resolve().is_relative_to(REAL_HOME) or Path(line).resolve().is_relative_to(temp)
    assert Path(out.splitlines()[0]).resolve() != REAL_HOME


def test_the_suite_is_never_the_bridges_own_service():
    assert "INVOCATION_ID" not in os.environ
    assert not config.STATE_DIR.resolve().is_relative_to(REAL_HOME / ".local")
    # Even read as the service's start, the restart record lands in the suite's own state folder.
    record = restarts.restart_info(config.STATE_DIR, environ={"INVOCATION_ID": "x"}, git=lambda *a: None)
    assert record["reason"] is not None
