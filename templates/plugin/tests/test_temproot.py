"""Each run's private temporary folder: held by a lock, removed by its run, swept once its run is dead.

The proofs that kill or stop a real process start one in a subprocess, so the lock it took is
the kernel's to drop, exactly as when a run dies.
"""

import fcntl
import os
import pathlib
import signal
import subprocess
import sys
import time

import pytest

ROOT = pathlib.Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import temproot

#: Starts a run's folder in the parent given, says where, and waits to be stopped.
HOLDER = """
import pathlib, sys, time
sys.path.insert(0, {scripts!r})
import temproot
temproot.exit_on_signals()
with temproot.run_folder(pathlib.Path(sys.argv[1])) as folder:
    (folder / "work.txt").write_text("x")
    print(folder, flush=True)
    time.sleep(60)
"""


def _holder(parent):
    script = HOLDER.format(scripts=str(ROOT / "scripts"))
    child = subprocess.Popen([sys.executable, "-c", script, str(parent)], stdout=subprocess.PIPE, text=True)
    folder = pathlib.Path(child.stdout.readline().strip())
    assert folder.is_dir()
    return child, folder


def _wait(child):
    try:
        return child.wait(timeout=30)
    finally:
        child.stdout.close()


def test_a_killed_run_s_temporary_files_are_swept_at_the_next_start(tmp_path):
    child, folder = _holder(tmp_path)
    child.kill()
    _wait(child)
    assert folder.exists(), "SIGKILL runs no clean-up, so the folder is still there"
    mine = temproot.start(tmp_path)
    assert not folder.exists()
    assert mine.is_dir()


def test_a_folder_whose_run_still_holds_its_lock_is_never_swept(tmp_path):
    child, folder = _holder(tmp_path)
    try:
        assert temproot.sweep(tmp_path) == []
        assert (folder / "work.txt").is_file()
    finally:
        child.terminate()
        _wait(child)


def test_a_terminated_run_removes_its_temporary_files(tmp_path):
    """CI cancels by SIGTERM, which by default runs no finally; here it does, and exits 143."""
    child, folder = _holder(tmp_path)
    child.send_signal(signal.SIGTERM)
    assert _wait(child) == 143
    assert not folder.exists()


def test_a_hung_up_run_removes_its_temporary_files_too(tmp_path):
    child, folder = _holder(tmp_path)
    child.send_signal(signal.SIGHUP)
    assert _wait(child) == 129
    assert not folder.exists()


def test_only_a_dead_run_s_own_kind_of_folder_is_swept(tmp_path):
    """Not another name, not a link, and not a folder still starting up, with no lock yet."""
    other = tmp_path / "keep-me"
    other.mkdir()
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    (outside / temproot.LOCK).touch()
    (tmp_path / "run-7").symlink_to(outside)
    starting = tmp_path / "run-8"
    starting.mkdir()
    assert temproot.sweep(tmp_path) == []
    assert other.is_dir()
    assert outside.is_dir()
    assert starting.is_dir()
    old = time.time() - temproot.GRACE - 1
    os.utime(starting, (old, old))
    assert temproot.sweep(tmp_path) == [starting]


def test_a_parent_that_is_a_link_is_refused(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    (tmp_path / "link").symlink_to(real)
    with pytest.raises(SystemExit, match="is a link"):
        temproot.start(tmp_path / "link")


def test_a_run_holds_its_lock_until_it_ends(tmp_path):
    folder = temproot.start(tmp_path)
    fd = os.open(folder / temproot.LOCK, os.O_RDWR)
    try:
        with pytest.raises(BlockingIOError):
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    finally:
        os.close(fd)
        temproot.remove(folder)


def test_anything_a_run_leaves_in_its_root_fails_the_run(tmp_path):
    (tmp_path / "pytest").mkdir()
    (tmp_path / "left.txt").write_text("x")
    assert temproot.leftovers(tmp_path, {"pytest"}) == [tmp_path / "left.txt"]
    assert temproot.leftovers(tmp_path / "missing", set()) == []


def test_removing_never_changes_a_folder_a_link_points_to(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    outside.chmod(0o755)
    (outside / "kept.txt").write_text("x")
    run = tmp_path / "run-1"
    (run / "shut").mkdir(parents=True)
    (run / "shut" / "inside.txt").write_text("x")
    (run / "link").symlink_to(outside)
    (run / "shut").chmod(0)
    temproot.remove(run)
    assert not run.exists()
    assert (outside.stat().st_mode & 0o777, (outside / "kept.txt").exists()) == (0o755, True)


def test_each_checkout_and_kind_has_a_parent_of_its_own(tmp_path):
    one = temproot.parent_for("tests", tmp_path / "a", base=tmp_path)
    assert one.parent == tmp_path
    assert one.name.startswith("gizmo-mcp-tests-")
    assert one != temproot.parent_for("tests", tmp_path / "b", base=tmp_path)
    assert one != temproot.parent_for("mutate", tmp_path / "a", base=tmp_path)
