"""Writing the owner's files: confined to their folder, whole or not at all, private, backed up."""

import os
import stat
import sys
from datetime import datetime

import pytest

from claude_voice import safefile
from claude_voice.safefile import Unsafe

# Owner-only file modes, symlinks and owner ids are what these tests check, and Windows has none
# of them as POSIX does. The bridge that installs plugins runs on Linux.
pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes and symlinks")

NOW = datetime(2026, 11, 15, 20, 15, 0)


def mode(path):
    return stat.S_IMODE(path.stat().st_mode)


def test_a_symlink_in_a_config_path_is_refused(tmp_path):
    config = tmp_path / ".config"
    (config / "fuel-mcp").mkdir(parents=True)
    (config / "fuel-mcp" / "real.toml").write_text("")
    (config / "fuel-mcp" / "config.toml").symlink_to(config / "fuel-mcp" / "real.toml")  # stays inside, still refused
    with pytest.raises(Unsafe, match="symlink"):
        safefile.inside(config, "fuel-mcp/config.toml")
    (config / "elsewhere").mkdir()
    (config / "linked").symlink_to(config / "elsewhere")
    with pytest.raises(Unsafe, match="symlink"):
        safefile.inside(config, "linked/config.toml")


def test_a_parent_step_or_an_absolute_path_is_refused(tmp_path):
    for bad in ("../x/config.toml", "fuel-mcp/../../x", "/etc/passwd", "", "a//b"):
        with pytest.raises(Unsafe):
            safefile.inside(tmp_path, bad)
    assert safefile.inside(tmp_path, "fuel-mcp/config.toml") == tmp_path / "fuel-mcp" / "config.toml"


def test_a_written_file_is_readable_by_its_owner_alone(tmp_path):
    target = tmp_path / "config.toml"
    target.write_text("old\n")
    os.chmod(target, 0o644)
    safefile.replace(target, "new\n")
    assert target.read_text() == "new\n" and mode(target) == 0o600


def test_a_symlinked_file_is_written_only_at_a_regular_file_its_owner_owns(tmp_path):
    real = tmp_path / "dotfiles" / "config.toml"
    real.parent.mkdir()
    real.write_text("old\n")
    link = tmp_path / "config.toml"
    link.symlink_to(real)
    safefile.replace(link, "new\n")
    assert link.is_symlink() and real.read_text() == "new\n"  # the link stays a link
    dangling = tmp_path / "dangling.toml"
    dangling.symlink_to(tmp_path / "nowhere")
    with pytest.raises(Unsafe):
        safefile.replace(dangling, "x")
    folder = tmp_path / "folder.toml"
    folder.symlink_to(real.parent)
    with pytest.raises(Unsafe):
        safefile.replace(folder, "x")
    assert not (tmp_path / "nowhere").exists()


@pytest.mark.parametrize("failure", [OSError("disk full"), KeyboardInterrupt()])
def test_a_temporary_file_is_removed_when_a_write_fails(tmp_path, monkeypatch, failure):
    target = tmp_path / "config.toml"
    target.write_text("old\n")

    def broken(fd):
        raise failure

    monkeypatch.setattr(os, "fsync", broken)
    with pytest.raises(type(failure)):
        safefile.replace(target, "new\n")
    assert target.read_text() == "old\n"  # whole or not at all
    assert [p.name for p in tmp_path.iterdir()] == ["config.toml"]


def test_a_backup_is_a_private_copy_next_to_the_file(tmp_path):
    target = tmp_path / "config.toml"
    target.write_text("old\n")
    first = safefile.backup(target, NOW)
    second = safefile.backup(target, NOW)  # the same second: never over the first
    assert first.name == "config.toml.20261115-201500.bak" and first.read_text() == "old\n"
    assert second != first and second.read_text() == "old\n"
    assert mode(first) == 0o600 and mode(second) == 0o600
