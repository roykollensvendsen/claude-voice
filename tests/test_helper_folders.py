"""The bridge's message helpers leave no folders behind in the temporary folder.

Each small helper session the bridge runs (the courier, the side-question reader, the
summary) works in a folder of its own, removed when the helper closes. A restart ends the
bridge with a kill, which runs no cleanup, so by 2026-11-15 over a hundred such folders had
piled up in /tmp. Each folder now holds a lock while it is in use, and the next start removes
the ones nobody holds.
"""

import os
import sys
import time

import pytest

from claude_voice import transcripts

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="Windows has no flock; there the sweep does nothing")

AN_HOUR_AGO = time.time() - 2 * 60 * 60


def killed(folder):
    """What a killed bridge leaves: the folder still there, its lock let go, and old."""
    os.close(folder._lock)
    folder._lock = None
    os.utime(folder.name, (AN_HOUR_AGO, AN_HOUR_AGO))
    return folder.name


def test_a_helper_folder_a_killed_bridge_left_is_removed_at_the_next_start(tmp_path):
    left = killed(transcripts.HelperFolder("claude-voice-msg-", dir=tmp_path))
    transcripts.sweep_helper_folders(tmp_path)
    assert not os.path.exists(left)


def test_a_helper_folder_still_in_use_is_never_removed(tmp_path):
    folder = transcripts.HelperFolder("claude-voice-msg-side-", dir=tmp_path)
    os.utime(folder.name, (AN_HOUR_AGO, AN_HOUR_AGO))
    try:
        transcripts.sweep_helper_folders(tmp_path)
        assert os.path.isdir(folder.name)
    finally:
        folder.cleanup()


def test_a_folder_from_before_the_lock_is_removed_only_once_it_is_old(tmp_path):
    """A folder left by a bridge from before this change has no lock to look at."""
    old, new = tmp_path / "claude-voice-msg-old", tmp_path / "claude-voice-msg-new"
    old.mkdir()
    new.mkdir()
    os.utime(old, (AN_HOUR_AGO, AN_HOUR_AGO))
    transcripts.sweep_helper_folders(tmp_path)
    assert (old.exists(), new.exists()) == (False, True)


def test_the_sweep_leaves_anything_not_named_as_a_helper_folder_alone(tmp_path):
    other, outside = tmp_path / "pytest-of-roy", tmp_path / "outside"
    other.mkdir()
    outside.mkdir()
    (outside / "kept.txt").write_text("x")
    link = tmp_path / "claude-voice-msg-link"
    link.symlink_to(outside)
    for place in (other, outside):
        os.utime(place, (AN_HOUR_AGO, AN_HOUR_AGO))
    transcripts.sweep_helper_folders(tmp_path)
    assert other.is_dir() and link.is_symlink() and (outside / "kept.txt").exists()


def test_closing_a_helper_folder_removes_it(tmp_path):
    folder = transcripts.HelperFolder("claude-voice-msg-", dir=tmp_path)
    folder.cleanup()
    folder.cleanup()  # a second close is harmless
    assert list(tmp_path.iterdir()) == []


def test_the_helpers_work_in_their_own_folder_under_the_name_that_hides_them(tmp_path):
    """The session tree and the peer hook know a helper by its folder's name."""
    with transcripts.HelperFolder("claude-voice-msg-digest-", dir=tmp_path) as cwd:
        assert os.path.basename(cwd).startswith("claude-voice-msg-")
        assert os.path.isdir(cwd)
    assert not os.path.exists(cwd)


def test_a_part_a_helper_shut_is_removed_too(tmp_path):
    """A plain removal that ignores errors would leave a shut folder behind without a word."""
    left = killed(transcripts.HelperFolder("claude-voice-msg-", dir=tmp_path))
    shut = os.path.join(left, "shut")
    os.mkdir(shut)
    open(os.path.join(shut, "inside.txt"), "w").close()
    os.chmod(shut, 0)
    os.utime(left, (AN_HOUR_AGO, AN_HOUR_AGO))
    transcripts.sweep_helper_folders(tmp_path)
    assert not os.path.exists(left)


def test_the_lock_s_name_never_stands_unlocked(tmp_path, monkeypatch):
    """The lock is taken under another name and renamed, so a sweep never finds it free."""
    import fcntl

    flock, seen = fcntl.flock, []

    def watched(fd, how):
        seen.append(sorted(os.listdir(next(tmp_path.iterdir()))))
        return flock(fd, how)

    monkeypatch.setattr(fcntl, "flock", watched)
    folder = transcripts.HelperFolder("claude-voice-msg-", dir=tmp_path)
    try:
        assert transcripts.HELPER_LOCK not in seen[0]
        assert sorted(os.listdir(folder.name)) == [transcripts.HELPER_LOCK]
    finally:
        folder.cleanup()


def test_a_bridge_killed_while_locking_leaves_a_folder_swept_only_once_it_is_old(tmp_path):
    young, old = tmp_path / "claude-voice-msg-young", tmp_path / "claude-voice-msg-old"
    for place in (young, old):
        place.mkdir()
        (place / (transcripts.HELPER_LOCK + "-starting")).write_text("")
    os.utime(old, (AN_HOUR_AGO, AN_HOUR_AGO))
    transcripts.sweep_helper_folders(tmp_path)
    assert (young.exists(), old.exists()) == (True, False)
