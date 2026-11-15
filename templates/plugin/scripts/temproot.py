#!/usr/bin/env python3
"""A private temporary folder per run, that the run removes and a later run sweeps if it was killed.

No test run, mutation run or ratchet run may leave a temporary file or folder behind,
interrupted runs included. Each run makes ``<system temp>/gizmo-mcp-<kind>-<checkout hash>/run-<n>``
and holds a lock on ``.lock`` inside it until the process ends. The kernel drops that lock on any
death, SIGKILL included, so the next start can tell a dead run's folder from a live one's. A pid
cannot tell them apart: in another pid namespace a live run's pid looks gone.

A start sweeps a folder only when all of these hold: it is named ``run-<digits>``; it lies directly
under this checkout's own parent; it is ours; no link was followed on the way; and its lock is free.

This module calls nothing at import, so the test conftest may import it before it moves the
temporary folder.
"""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import os
import pathlib
import re
import shutil
import signal
import stat
import sys
import tempfile
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

PACKAGE = "gizmo-mcp"
LOCK = ".lock"
RUN = re.compile(r"run-\d+")
#: A folder with no lock file yet may belong to a run that is just starting; it is left alone
#: until it is this many seconds old.
GRACE = 600.0
#: The exit status a shell gives a process ended by each signal.
SIGNALS = {signal.SIGTERM: 143, signal.SIGHUP: 129}
#: The lock files held by this process, kept open until it ends.
_held: list[int] = []


def parent_for(kind: str, checkout: pathlib.Path, base: pathlib.Path | None = None) -> pathlib.Path:
    """Where this checkout's runs of one kind keep their folders: named after both, outside the checkout."""
    digest = hashlib.sha256(str(checkout.resolve()).encode()).hexdigest()[:8]
    return pathlib.Path(tempfile.gettempdir() if base is None else base) / f"{PACKAGE}-{kind}-{digest}"


def exit_on_signals() -> None:
    """Turn SIGTERM and SIGHUP into SystemExit, so every `finally` and `with` still runs.

    CI cancels a job with SIGTERM, which by default ends the process without running a `finally`.
    """

    def stop(signum: int, _frame: object) -> None:
        # RULE: a terminated run removes its temporary files
        raise SystemExit(SIGNALS[signal.Signals(signum)])

    for signum in SIGNALS:
        signal.signal(signum, stop)


def _ours(path: pathlib.Path) -> os.stat_result | None:
    """The folder's own status, when it is a real folder of ours; None for a link or anyone else's."""
    try:
        status = os.lstat(path)
    except OSError:
        return None
    return status if stat.S_ISDIR(status.st_mode) and status.st_uid == os.getuid() else None


def _lock_is_free(folder: pathlib.Path) -> bool:
    """Whether no live run holds the folder's lock; opened with no link followed."""
    try:
        fd = os.open(folder / LOCK, os.O_RDWR | os.O_NOFOLLOW)
    except FileNotFoundError:
        status = _ours(folder)
        return status is not None and time.time() - status.st_mtime > GRACE
    except OSError:
        return False
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return False
    finally:
        os.close(fd)
    return True


def stale(folder: pathlib.Path) -> bool:
    """Whether `folder` is a dead run's, and so may be swept."""
    if not RUN.fullmatch(folder.name) or _ours(folder) is None:
        return False
    # RULE: a folder whose run still holds its lock is never swept
    return _lock_is_free(folder)


def sweep(parent: pathlib.Path) -> list[pathlib.Path]:
    """Remove what earlier runs, killed before they could clean up, left directly under `parent`."""
    if _ours(parent) is None:
        return []
    swept = []
    for folder in sorted(parent.iterdir()):
        # RULE: a killed run's temporary files are swept at the next start
        if stale(folder):
            remove(folder)
            swept.append(folder)
    return swept


def start(parent: pathlib.Path) -> pathlib.Path:
    """Sweep `parent`, then make this run's folder in it and hold its lock until the process ends."""
    if parent.is_symlink():
        msg = f"{parent} is a link; remove it, so no run's files are kept through it"
        raise SystemExit(msg)
    parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    sweep(parent)
    for suffix in ("", *map(str, range(1, 1000))):
        folder = parent / f"run-{os.getpid()}{suffix}"
        try:
            # The parent again, in case another run just finished and removed it, empty.
            parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            folder.mkdir(mode=0o700)
        except FileExistsError:
            continue
        fd = os.open(folder / LOCK, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX)
        _held.append(fd)
        return folder
    msg = f"no free run folder in {parent}; remove the folders there"
    raise SystemExit(msg)


@contextlib.contextmanager
def run_folder(parent: pathlib.Path) -> Iterator[pathlib.Path]:
    """Make this run's folder, removed when the block ends, however it ends short of SIGKILL."""
    folder = start(parent)
    try:
        yield folder
    finally:
        finish(folder)


def finish(folder: pathlib.Path) -> None:
    """Remove a run's folder, and its parent too once no other run is in it."""
    remove(folder)
    with contextlib.suppress(OSError):
        folder.parent.rmdir()


def leftovers(root: pathlib.Path, keep: set[str]) -> list[pathlib.Path]:
    """What a run left in its root, apart from the names in `keep`."""
    found = []
    for entry in sorted(root.iterdir()) if root.is_dir() else []:
        # RULE: anything a run leaves in its root fails the run
        if entry.name not in keep:
            found.append(entry)  # noqa: PERF401 - the rule's line stays its own
    return found


def _open_up(place: pathlib.Path) -> None:
    """Give the owner back every right on one folder or file; a link is left alone."""
    if place.is_symlink():
        return
    with contextlib.suppress(OSError):
        place.chmod(place.stat().st_mode | stat.S_IRWXU)


def _retry(func: Callable[..., Any], path: str, _exc: BaseException) -> None:
    """Let a refused delete through: open up the path and its folder, then try again."""
    for place in (pathlib.Path(path).parent, pathlib.Path(path)):
        _open_up(place)
    with contextlib.suppress(FileNotFoundError):
        func(path)


def remove(folder: pathlib.Path) -> None:
    """Delete a folder, even where a test took away the rights to read or delete it, following no link."""
    if folder.is_symlink() or folder.is_file():
        folder.unlink()
        return
    if not folder.exists():
        return
    # A folder a test shut (mode 0) cannot even be listed, so open every folder first.
    _open_up(folder)
    for place, folders, _ in os.walk(folder):
        for name in folders:
            _open_up(pathlib.Path(place) / name)
    shutil.rmtree(folder, onexc=_retry)


if __name__ == "__main__":
    sys.stdout.write(__doc__ or "")
