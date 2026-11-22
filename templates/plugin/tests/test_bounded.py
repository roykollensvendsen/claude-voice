"""A test that waits on another process fails when its own time is up, and leaves nothing running.

No limit is put on a test or a run from outside. Each wait inside a test carries its own bound,
so a child that never answers fails that one test instead of holding a mutation run for hours.
"""

import contextlib
import os
import pathlib
import signal
import subprocess
import sys
import time

import pytest
from bounded import line_within, run_bounded


def test_a_child_that_never_writes_fails_within_the_bound():
    with subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"], stdout=subprocess.PIPE, text=True
    ) as child:
        started = time.monotonic()
        try:
            with pytest.raises(pytest.fail.Exception, match="no line within 1 s"):
                line_within(child.stdout, 1)
        finally:
            child.kill()
    assert time.monotonic() - started < 30


def test_two_lines_written_at_once_are_both_read():
    """The first read may take both into its buffer; the second must still find its line."""
    with subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import sys, time; sys.stdout.write('one\\ntwo\\n'); sys.stdout.flush(); time.sleep(60)",
        ],
        stdout=subprocess.PIPE,
        text=True,
    ) as child:
        try:
            assert line_within(child.stdout, 30) == "one\n"
            assert line_within(child.stdout, 30) == "two\n"
        finally:
            child.kill()


def _gone(group: int) -> bool:
    try:
        os.killpg(group, 0)
    except ProcessLookupError:
        return True
    return False


def _ended(pid: int) -> bool:
    """Whether `pid` has ended, counting a zombie not yet reaped as ended."""
    try:
        state = pathlib.Path(f"/proc/{pid}/stat").read_text(encoding="utf-8").rsplit(")", 1)[1].split()[0]
    except (FileNotFoundError, ProcessLookupError):
        return True
    return state == "Z"


@pytest.mark.skipif(not pathlib.Path("/proc").is_dir(), reason="reads the sleep's state from /proc")
def test_a_child_that_overruns_leaves_nothing_of_its_group_running(tmp_path: pathlib.Path) -> None:
    """A shell's `sleep` holds its output; plain `timeout=` would end the shell and leave the sleep."""
    leader, sleeper = tmp_path / "leader", tmp_path / "sleeper"
    script = f"echo $$ > {leader}; sleep 30 & echo $! > {sleeper}; wait"
    started = time.monotonic()
    with pytest.raises(pytest.fail.Exception, match="did not finish within 1 s"):
        run_bounded(["sh", "-c", script], 1, capture_output=True, text=True)
    # Killing the shell alone would wait out the sleep that holds its output, then find it gone.
    assert time.monotonic() - started < 15, "the bound waited for the sleep to end by itself"
    group, grandchild = int(leader.read_text(encoding="utf-8")), int(sleeper.read_text(encoding="utf-8"))
    try:
        deadline = time.monotonic() + 10  # a killed sleep is reaped by init, which takes a moment
        while not (_ended(grandchild) and _gone(group)) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert _ended(grandchild), "the shell's sleep was left running"
        assert _gone(group), "a process of the overrunning child's group was left running"
    finally:
        _end_if_still_the_sleep(grandchild)


def _end_if_still_the_sleep(pid: int) -> None:
    """End `pid` by itself, but only while it is still this test's sleep.

    A failing run then leaves nothing behind, and a pid the system has since given to another
    process is left alone.
    """
    try:
        command = pathlib.Path(f"/proc/{pid}/cmdline").read_bytes()
    except (FileNotFoundError, ProcessLookupError):
        return
    if command == b"sleep\x0030\x00":
        with contextlib.suppress(ProcessLookupError):
            os.kill(pid, signal.SIGKILL)


def test_a_child_that_finishes_gives_what_it_wrote() -> None:
    done = run_bounded([sys.executable, "-c", "print('hi')"], capture_output=True, text=True)
    assert (done.returncode, done.stdout) == (0, "hi\n")
