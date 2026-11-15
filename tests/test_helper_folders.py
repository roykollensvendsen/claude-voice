"""The bridge's message helpers leave no folders behind in the temporary folder.

Each small helper session the bridge runs (the courier, the side-question reader, the
summary) works in a folder of its own, removed when the helper closes. A restart ends the
bridge with a kill, which runs no cleanup, so by 2026-11-15 over a hundred such folders had
piled up in /tmp. Each folder now holds a lock while it is in use, and the next start removes
the ones nobody holds.

How the lock and the sweep work is tested once, in test_held_folder.py; these check
that the bridge's helpers use them.
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


def test_the_helpers_work_in_their_own_folder_under_the_name_that_hides_them(tmp_path):
    """The session tree and the peer hook know a helper by its folder's name."""
    with transcripts.HelperFolder("claude-voice-msg-digest-", dir=tmp_path) as cwd:
        assert os.path.basename(cwd).startswith("claude-voice-msg-")
        assert os.path.isdir(cwd)
    assert not os.path.exists(cwd)


def test_closing_a_helper_folder_removes_it(tmp_path):
    folder = transcripts.HelperFolder("claude-voice-msg-", dir=tmp_path)
    folder.cleanup()
    folder.cleanup()  # a second close is harmless
    assert list(tmp_path.iterdir()) == []
