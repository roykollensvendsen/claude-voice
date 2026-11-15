"""A folder in the temporary folder, locked for as long as its holder runs.

A kill runs no cleanup, so whatever a killed holder was working in stays behind. Each holder
therefore keeps a lock open inside its folder, and whoever starts next sweeps away every
folder of its kind whose lock nobody holds. A lock is held by an open file, not by a process
number, so a holder in another process namespace, or a reused number, never fools the sweep.

The bridge's message helpers and the quality ratchet both work this way.
"""

from __future__ import annotations

import contextlib
import logging
import os
import shutil
import stat
import sys
import tempfile
import time
from pathlib import Path

log = logging.getLogger(__name__)

#: The lock a held folder keeps open while it is in use.
LOCK = ".in-use"
#: How old a folder with no lock must be before it counts as left by a killed holder. A guess:
#: long enough for any holder to have gone from making its folder to locking it.
GRACE_SECONDS = 60 * 60


class HeldFolder:
    """A new folder named `prefix` and some letters, locked until `remove`.

    On Windows, which has no flock, the folder is made and removed but never swept.
    """

    def __init__(self, prefix: str, dir: Path | None = None) -> None:
        self.path = Path(tempfile.mkdtemp(prefix=prefix, dir=dir))
        self.name = str(self.path)
        self._lock: int | None = None
        if sys.platform != "win32":
            import fcntl

            # Locked under another name, then renamed, so the lock's name never stands unlocked.
            starting = self.path / (LOCK + "-starting")
            self._lock = os.open(starting, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
            fcntl.flock(self._lock, fcntl.LOCK_EX)
            starting.rename(self.path / LOCK)

    def remove(self) -> bool:
        """Let go of the lock and remove the folder; say whether it is gone."""
        if self._lock is not None:
            os.close(self._lock)
            self._lock = None
        return remove(self.path)

    def __enter__(self) -> str:
        return self.name

    def __exit__(self, *_exc: object) -> None:
        self.remove()


def left_behind(path: Path) -> bool:
    """Whether `path` is a held folder of this user's whose lock nobody holds."""
    if sys.platform == "win32":
        return False
    import fcntl

    try:
        found = path.lstat()
    except FileNotFoundError:
        return False
    if not stat.S_ISDIR(found.st_mode) or found.st_uid != os.getuid():
        return False  # a link is never followed, and another user's folder never touched
    try:
        lock = os.open(path / LOCK, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        # Made before the lock existed, or by a holder killed between making it and locking it.
        return time.time() - found.st_mtime > GRACE_SECONDS
    except OSError:
        return False
    try:
        # RULE: a held folder still in use is never removed
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return False
    finally:
        os.close(lock)
    return True


def sweep(place: Path, prefix: str) -> list[str]:
    """Remove every folder in `place` named `prefix`... whose holder is gone; name each one."""
    if sys.platform == "win32":
        return []
    removed = []
    for path in sorted(place.glob(prefix + "*")):
        # RULE: a held folder a killed holder left is removed by the next sweep
        if left_behind(path):
            remove(path)
            removed.append(path.name)
    return removed


def remove(folder: Path) -> bool:
    """Delete a folder, giving back the rights to any part its holder shut; say what stays."""
    for place, folders, _ in os.walk(folder):
        for name in folders:
            inner = Path(place) / name
            if not inner.is_symlink():
                with contextlib.suppress(OSError):
                    inner.chmod(inner.stat().st_mode | stat.S_IRWXU)
    shutil.rmtree(folder, ignore_errors=True)
    if folder.exists():
        log.warning("could not remove all of %s; remove it by hand", folder)
        return False
    return True
