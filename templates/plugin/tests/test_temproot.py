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


def test_an_interrupted_run_removes_its_temporary_files_too(tmp_path):
    """GitHub cancels a job with SIGINT first; the run stops as Ctrl-C stops it, cleaning up."""
    child, folder = _holder(tmp_path)
    child.send_signal(signal.SIGINT)
    assert _wait(child) != 0
    assert not folder.exists()


#: A run whose clean-up takes a second, and says when it starts and when it is done.
SLOW_CLEANUP = """
import pathlib, sys, time
sys.path.insert(0, {scripts!r})
import temproot
temproot.exit_on_signals()
parent = pathlib.Path(sys.argv[1])
with temproot.run_folder(parent) as folder:
    try:
        print(folder, flush=True)
        time.sleep(60)
    finally:
        (parent.parent / "cleaning").touch()
        time.sleep(1)
        (parent.parent / "cleaned").touch()
"""


def test_a_second_stop_does_not_cut_the_cleanup_short(tmp_path):
    """GitHub cancels with SIGINT and then SIGTERM; the second must find the first one's clean-up running on."""
    parent = tmp_path / "runs"
    script = SLOW_CLEANUP.format(scripts=str(ROOT / "scripts"))
    child = subprocess.Popen([sys.executable, "-c", script, str(parent)], stdout=subprocess.PIPE, text=True)
    folder = pathlib.Path(child.stdout.readline().strip())
    child.send_signal(signal.SIGINT)
    deadline = time.monotonic() + 30
    while not (tmp_path / "cleaning").exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    child.send_signal(signal.SIGTERM)
    _wait(child)
    assert (tmp_path / "cleaned").exists(), "the second signal cut the clean-up short"
    assert not folder.exists()


def test_a_hung_up_run_removes_its_temporary_files_too(tmp_path):
    child, folder = _holder(tmp_path)
    child.send_signal(signal.SIGHUP)
    assert _wait(child) == 129
    assert not folder.exists()


def test_only_a_dead_run_s_own_kind_of_folder_is_swept(tmp_path):
    """Not another name, not a link, and not a folder still starting up, with no lock yet."""
    parent = tmp_path / "parent"
    other = parent / "keep-me"
    other.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / temproot.LOCK).touch()
    (parent / "run-7").symlink_to(outside)
    starting = parent / "starting-x1"
    starting.mkdir()
    (starting / temproot.LOCK).touch()
    assert temproot.sweep(parent) == []
    assert other.is_dir()
    assert outside.is_dir()
    assert starting.is_dir()
    old = time.time() - temproot.GRACE - 1
    os.utime(starting, (old, old))
    assert temproot.sweep(parent) == [starting]


def test_a_run_s_folder_is_locked_before_it_takes_a_run_s_name(tmp_path, monkeypatch):
    """Another run sweeping just as this one takes its lock must find nothing it may take.

    The other run's sweep is played inside this run's own call to lock: at that moment the
    folder exists, but its lock is not held yet. Named as a run's then, it would be swept.
    """
    real_flock = fcntl.flock
    calls = []

    def flock_after_a_sweep(fd, operation):
        calls.append(operation)
        if len(calls) == 1:
            temproot.sweep(tmp_path)
        return real_flock(fd, operation)

    monkeypatch.setattr(temproot.fcntl, "flock", flock_after_a_sweep)
    folder = temproot.start(tmp_path)
    try:
        assert temproot.RUN.fullmatch(folder.name)
        assert (folder / temproot.LOCK).is_file(), "the other run's sweep took this run's folder"
        assert [p.name for p in tmp_path.iterdir()] == [folder.name]
    finally:
        temproot.remove(folder)


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
