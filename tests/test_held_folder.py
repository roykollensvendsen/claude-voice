"""A held folder: a folder in the temporary folder, locked while its holder runs.

The bridge's message helpers and the quality ratchet each work in such a folder. A kill runs
no cleanup, so whoever starts next sweeps away every folder whose lock nobody holds.
"""

import os
import sys
import time

import pytest

from claude_voice import held_folder

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="Windows has no flock; there the sweep does nothing")

PREFIX = "claude-voice-test-"
AN_HOUR_AGO = time.time() - 2 * 60 * 60


def let_go(folder):
    """What a killed holder leaves: the folder still there, and its lock free."""
    os.close(folder._lock)
    folder._lock = None
    return folder.path


def test_a_held_folder_a_killed_holder_left_is_removed_by_the_next_sweep(tmp_path):
    left = let_go(held_folder.HeldFolder(PREFIX, dir=tmp_path))
    assert held_folder.sweep(tmp_path, PREFIX) == [left.name]
    assert not left.exists()


def test_a_held_folder_still_in_use_is_never_removed(tmp_path):
    held = held_folder.HeldFolder(PREFIX, dir=tmp_path)
    os.utime(held.path, (AN_HOUR_AGO, AN_HOUR_AGO))
    try:
        assert held_folder.sweep(tmp_path, PREFIX) == []
        assert held.path.is_dir()
    finally:
        held.remove()
    assert not held.path.exists()


def test_a_folder_with_no_lock_is_removed_only_once_it_is_old(tmp_path):
    """Made before the lock existed, or by a holder killed between making it and locking it."""
    old, new = tmp_path / f"{PREFIX}old", tmp_path / f"{PREFIX}new"
    for place in (old, new):
        place.mkdir()
        (place / (held_folder.LOCK + "-starting")).write_text("")
    os.utime(old, (AN_HOUR_AGO, AN_HOUR_AGO))
    held_folder.sweep(tmp_path, PREFIX)
    assert (old.exists(), new.exists()) == (False, True)


def test_the_sweep_leaves_links_and_other_names_alone(tmp_path):
    other, outside = tmp_path / "pytest-of-roy", tmp_path / "outside"
    other.mkdir()
    outside.mkdir()
    (outside / "kept.txt").write_text("x")
    (tmp_path / f"{PREFIX}link").symlink_to(outside)
    for place in (other, outside):
        os.utime(place, (AN_HOUR_AGO, AN_HOUR_AGO))
    held_folder.sweep(tmp_path, PREFIX)
    assert other.is_dir() and (tmp_path / f"{PREFIX}link").is_symlink() and (outside / "kept.txt").exists()


def test_a_part_its_holder_shut_is_removed_too(tmp_path):
    left = let_go(held_folder.HeldFolder(PREFIX, dir=tmp_path))
    (left / "shut").mkdir()
    (left / "shut" / "inside.txt").write_text("x")
    os.chmod(left / "shut", 0)
    held_folder.sweep(tmp_path, PREFIX)
    assert not left.exists()


def test_the_lock_s_name_never_stands_unlocked(tmp_path, monkeypatch):
    import fcntl

    flock, seen = fcntl.flock, []

    def watched(fd, how):
        seen.append(sorted(os.listdir(next(tmp_path.iterdir()))))
        return flock(fd, how)

    monkeypatch.setattr(fcntl, "flock", watched)
    held = held_folder.HeldFolder(PREFIX, dir=tmp_path)
    try:
        assert held_folder.LOCK not in seen[0]
        assert sorted(os.listdir(held.path)) == [held_folder.LOCK]
    finally:
        held.remove()


def test_removing_says_whether_the_folder_is_gone(tmp_path):
    held = held_folder.HeldFolder(PREFIX, dir=tmp_path)
    assert held.remove() is True
    assert held.remove() is True  # a second remove is harmless
    with held_folder.HeldFolder(PREFIX, dir=tmp_path) as name:
        assert os.path.isdir(name)
    assert not os.path.exists(name)
