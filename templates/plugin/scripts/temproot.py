#!/usr/bin/env python3
"""A private temporary folder per run, that the run removes and a later run sweeps if it was killed.

No test run, mutation run or ratchet run may leave a temporary file or folder behind,
interrupted runs included. Each run makes ``<system temp>/gizmo-mcp-<kind>-<checkout hash>/run-<n>``
and holds a lock on ``.lock`` inside it until the process ends. The kernel drops that lock on any
death, SIGKILL included, so the next start can tell a dead run's folder from a live one's. A pid
cannot tell them apart: in another pid namespace a live run's pid looks gone.

A run's folder is made as ``starting-<random>``, locked, and only then renamed to its run name,
so a sweep never meets a run's name without its lock held. A run killed in between leaves a
starting folder, swept once it is old.

The parent must be a real folder of ours that no one else may open: a parent another user made
first, open to all, would see every run's files. Anything else stops the run, naming the folder.

A start sweeps a folder only when all of these hold: it is named ``run-<digits>``, or is a starting
folder older than ``STARTING_SECONDS``; it lies directly under this checkout's own parent; it is
ours; no link was followed on the way; and its lock is free.

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
import secrets
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
#: A run's folder before it is locked and renamed.
STARTING = re.compile(r"starting-\w+")
#: A starting folder, or a run's with no lock file, may belong to a run that is starting now;
#: it is left alone until it is this many seconds old. A guess: long enough for any run to go
#: from making its folder to locking it, as the bridge's held folders allow.
STARTING_SECONDS = 60 * 60
#: The exit status a shell gives a process ended by each signal.
SIGNALS = {signal.SIGINT: 130, signal.SIGTERM: 143, signal.SIGHUP: 129}
#: The lock files held by this process, kept open until it ends.
_held: list[int] = []


def parent_for(kind: str, checkout: pathlib.Path, base: pathlib.Path | None = None) -> pathlib.Path:
    """Where this checkout's runs of one kind keep their folders: named after both, outside the checkout."""
    digest = hashlib.sha256(str(checkout.resolve()).encode()).hexdigest()[:8]
    return pathlib.Path(tempfile.gettempdir() if base is None else base) / f"{PACKAGE}-{kind}-{digest}"


def exit_on_signals() -> None:
    """Turn a stop from outside into the exit Ctrl-C gives, so every `finally` and `with` still runs.

    SIGINT raises KeyboardInterrupt; SIGTERM and SIGHUP, which by default end the process without
    running a `finally`, raise SystemExit. GitHub cancels a job with SIGINT and then SIGTERM, so the
    first stop makes the process deaf to all three before its clean-up starts.
    """

    def stop(signum: int, _frame: object) -> None:
        # RULE: a second stop does not cut the cleanup short
        for each in SIGNALS:
            signal.signal(each, signal.SIG_IGN)
        if signum == signal.SIGINT:
            raise KeyboardInterrupt
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


def _old(status: os.stat_result) -> bool:
    return time.time() - status.st_mtime > STARTING_SECONDS


def _lock_is_free(folder: pathlib.Path, status: os.stat_result) -> bool:
    """Whether no live run holds the folder's lock; opened with no link followed.

    `status` is the folder's own, from the one look the sweep took at it.
    """
    try:
        fd = os.open(folder / LOCK, os.O_RDWR | os.O_NOFOLLOW)
    except FileNotFoundError:
        return _old(status)
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
    status = _ours(folder)
    if status is None:
        return False
    if STARTING.fullmatch(folder.name):
        # RULE: a run's folder is locked before it takes a run's name
        return _old(status) and _lock_is_free(folder, status)
    if not RUN.fullmatch(folder.name):
        return False
    # RULE: a folder whose run still holds its lock is never swept
    return _lock_is_free(folder, status)


def _refuse(parent: pathlib.Path, what: str) -> SystemExit:
    return SystemExit(
        f"{parent} {what}, so a run's files there could be seen by others. Check what it is,"
        f" remove it with `rm -r {parent}`, and run again; the run makes it afresh."
    )


def _names_in(parent: pathlib.Path) -> list[str] | None:
    """What is in `parent`, read through the folder checked; None when it is not there.

    Opened with no link followed, and listed through that same opening, so the folder checked
    is the folder read, even if another run removes it in between.
    """
    try:
        fd = os.open(parent, os.O_RDONLY | os.O_NOFOLLOW | os.O_DIRECTORY)
    except FileNotFoundError:
        return None
    except OSError as error:  # a link (ELOOP), or not a folder at all (ENOTDIR)
        raise _refuse(parent, "is a link or not a folder") from error
    try:
        status = os.fstat(fd)
        # RULE: a run keeps its files only in a folder of its own that no one else can open
        if status.st_uid != os.getuid() or status.st_mode & 0o077:
            raise _refuse(parent, f"is not yours alone (mode {stat.filemode(status.st_mode)})")
        return sorted(os.listdir(fd))  # noqa: PTH208 - listed through the opening checked, not by path
    finally:
        os.close(fd)


def sweep(parent: pathlib.Path) -> list[pathlib.Path]:
    """Remove what earlier runs, killed before they could clean up, left directly under `parent`.

    Stops the run when `parent` is not a folder of ours alone; finds nothing when it is gone.
    """
    names = _names_in(parent)
    swept = []
    for folder in (parent / name for name in names or []):
        # RULE: a killed run's temporary files are swept at the next start
        if stale(folder):
            remove(folder)
            swept.append(folder)
    return swept


def start(parent: pathlib.Path) -> pathlib.Path:
    """Sweep `parent`, then make this run's folder in it and hold its lock until the process ends."""
    _make(parent)
    sweep(parent)
    starting = _locked_starting_folder(parent)
    for suffix in ("", *map(str, range(1, 1000))):
        folder = parent / f"run-{os.getpid()}{suffix}"
        try:
            folder.mkdir(mode=0o700)  # claims the name, so the rename never replaces another's
        except FileExistsError:
            continue
        starting.rename(folder)
        return folder
    remove(starting)
    msg = f"no free run folder in {parent}; remove the folders there"
    raise SystemExit(msg)


def _make(parent: pathlib.Path) -> None:
    """Make `parent` for this user alone, or check the one there is; stop the run if it is not."""
    parent.parent.mkdir(parents=True, exist_ok=True)
    while True:  # another run's finish may remove it, empty, between the two steps
        with contextlib.suppress(FileExistsError):
            parent.mkdir(mode=0o700)
        if _names_in(parent) is not None:
            return


def _locked_starting_folder(parent: pathlib.Path) -> pathlib.Path:
    """A new folder under a starting name, with this process holding its lock until it ends."""
    while True:
        starting = parent / f"starting-{secrets.token_hex(8)}"
        try:
            starting.mkdir(mode=0o700)
        except FileExistsError:
            continue
        except FileNotFoundError:
            # Another run just finished and removed the parent, empty; make it again.
            _make(parent)
            continue
        fd = os.open(starting / LOCK, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX)
        _held.append(fd)
        return starting


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
