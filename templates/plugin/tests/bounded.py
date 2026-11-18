"""Waits on another process that end in a failure of the waiting test, never in a hang.

No limit is put on a test or a run from outside (the owner's rule). Each wait inside a test carries
its own bound instead, so a child that never answers fails that one test, and a mutant that
hangs counts as killed the way one that crashes does.
"""

import os
import queue
import signal
import subprocess
import threading

import pytest

#: The bound on each wait, in seconds: one number for every repo, generous enough that it
#: fires only on a real hang.
WITHIN = 300


def run_bounded(cmd, within=WITHIN, *, check=False, input=None, capture_output=False, **kw):  # noqa: A002
    """`subprocess.run` with a bound that, when it runs out, ends the child and all it started.

    `timeout=` alone kills only the direct child. A grandchild under `sh -c` or `uv run` would
    keep running into the next tests. So the child gets a group of its own, and on time the
    whole group gets SIGKILL: it cannot be caught, so `uv` cannot pass it on a second time.
    """
    if capture_output:
        kw["stdout"] = kw["stderr"] = subprocess.PIPE
    if input is not None:
        kw["stdin"] = subprocess.PIPE
    with subprocess.Popen(cmd, start_new_session=True, **kw) as child:
        try:
            out, err = child.communicate(input, timeout=within)
        except subprocess.TimeoutExpired:
            os.killpg(child.pid, signal.SIGKILL)
            out, err = child.communicate()
            pytest.fail(f"{cmd!r} did not finish within {within} s; it wrote:\n{out}\n{err}")
    done = subprocess.CompletedProcess(cmd, child.returncode, out, err)
    if check:
        done.check_returncode()
    return done


def line_within(stream, seconds):
    """The next line from `stream`; a failure, not a hang, if none comes within `seconds`.

    The read runs on a thread of its own, since a pipe's readline cannot be given a limit.
    A child that never answers is then the test's failure, which kills it as it unwinds.
    """
    got = queue.Queue()
    threading.Thread(target=lambda: got.put(stream.readline()), daemon=True).start()
    try:
        return got.get(timeout=seconds)
    except queue.Empty:
        pytest.fail(f"no line within {seconds} s")
