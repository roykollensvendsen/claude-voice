"""The bridge's own lock around uv's work on an environment, so two such steps take turns.

uv's cache prune, a plugin's sync before it starts, and the updater's install all change what
uv keeps. Each takes this lock first, so a prune never runs while a sync is half done. The lock
is an open file, not a process number, so a killed holder lets go by itself.

On Windows, which has no flock, nothing is locked; there each step relies on uv's own lock.
"""

from __future__ import annotations

import contextlib
import os
import sys
import time
from collections.abc import Iterator
from pathlib import Path

#: How often a waiting step looks again.
POLL_SECONDS = 0.1


class Busy(TimeoutError):
    """Another step held the lock for longer than this one would wait."""


@contextlib.contextmanager
def held(path: Path, seconds: float) -> Iterator[None]:
    """Hold the lock at `path` for the block, waiting no more than `seconds` for it."""
    if sys.platform == "win32":
        yield
        return
    import fcntl

    path = path.expanduser()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        deadline = time.monotonic() + seconds
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise Busy(f"other uv work held {path} for more than {seconds:g} s") from None
                time.sleep(POLL_SECONDS)
        yield
    finally:
        os.close(lock)  # closing it lets go of the lock
