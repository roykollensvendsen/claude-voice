"""Writing the owner's own files safely: confined to their folder, private, backed up first,
and replaced whole or not at all.

Used by ``plugins install`` and ``home set``, which change files the owner keeps. A file is
written as a temporary one in the same folder, made owner-only, synced and then moved into
place, so a crash or Ctrl-C leaves the old file or the new one, never half of either, and
no temporary file behind.
"""

from __future__ import annotations

import contextlib
import os
import shlex
import stat
import sys
import tempfile
from datetime import datetime
from pathlib import Path


class Unsafe(ValueError):
    """A path or file this refuses to write; the message says why."""


def inside(root: Path, relative: str) -> Path:
    """`root` joined with `relative`, refused when `relative` is absolute or steps up, or
    when any step below `root` is a symlink, wherever it points."""
    parts = relative.split("/")
    if not relative or relative.startswith("/") or any(p in ("", ".", "..") for p in parts):
        raise Unsafe(f"{relative!r} is not a plain path inside {root}")
    step = root
    for part in parts:
        step = step / part
        # RULE: a symlink in a config path is refused
        if step.is_symlink():
            raise Unsafe(f"{step} is a symlink; install writes only to plain folders and files. Remove it, or move it")
    if not step.resolve().is_relative_to(root.resolve()):
        raise Unsafe(f"{step} lands outside {root}")
    return step


def target(path: Path) -> Path:
    """Where writing `path` really writes: a symlink's target, if that is a regular file the
    owner owns, so the link stays a link; refused otherwise."""
    if not path.is_symlink():
        if path.exists() and not path.is_file():
            raise Unsafe(f"{path} is not a regular file")
        return path
    real = path.resolve()
    # RULE: a symlinked file is written only at a regular file its owner owns
    if not real.is_file() or not _owned(real):
        raise Unsafe(f"{path} links to {real}, which is not a regular file of yours")
    return real


def _owned(path: Path) -> bool:
    if sys.platform == "win32":  # no owner ids to compare
        return True
    return path.stat().st_uid == os.getuid()


def replace(path: Path, text: str) -> None:
    """Write `text` as the whole of `path`, owner-only, or leave the old file as it was."""
    real = target(path)
    fd, spare = tempfile.mkstemp(dir=real.parent, prefix=f".{real.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            if sys.platform != "win32":  # Windows has no owner-only mode to set
                # RULE: a written file is readable by its owner alone
                os.fchmod(f.fileno(), 0o600)
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(spare, real)
    except BaseException:
        # RULE: a temporary file is removed when a write fails
        with contextlib.suppress(OSError):
            os.unlink(spare)
        raise


def backup(path: Path, now: datetime) -> Path:
    """A private copy of `path` next to it, named `<file>.<YYYYMMDD-HHMMSS>.bak`, never over
    an earlier one."""
    stamp = now.strftime("%Y%m%d-%H%M%S")
    body = path.read_bytes()
    for n in range(100):
        spare = path.with_name(f"{path.name}.{stamp}{f'-{n}' if n else ''}.bak")
        try:
            fd = os.open(spare, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            continue
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(body)
                f.flush()
                os.fsync(f.fileno())
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(spare)
            raise
        return spare
    raise Unsafe(f"{path} has a hundred backups from {stamp} already")


def private_folder(folder: Path, narrow: bool = True) -> bool:
    """Make `folder` owner-only if it is missing. One that exists and is open to others is
    narrowed to owner-only when `narrow` is set; whether it was."""
    if not folder.exists():
        folder.mkdir(mode=0o700, parents=True)
        os.chmod(folder, 0o700)  # mkdir's mode is narrowed by the umask, never widened
        return False
    if not narrow or sys.platform == "win32":
        return False
    # RULE: an owner-only folder that is wider is narrowed
    if stat.S_IMODE(folder.stat().st_mode) & 0o077:
        os.chmod(folder, 0o700)
        return True
    return False


def outside_repositories(path: Path) -> Path:
    """`path`, refused when, with every link followed, it lands inside a git repository: a
    ~/.config linked into a dotfiles repository would put a private file where a commit
    publishes it."""
    real = path.resolve()
    for folder in real.parents:
        # RULE: a private file inside a git repository is refused
        if (folder / ".git").exists():
            raise Unsafe(
                f"{path} would be inside the git repository at {folder}, where a commit could publish it. "
                "Keep ~/.config outside any git repository, then run this again"
            )
    return path


def shown(file: Path, home: Path) -> str:
    """A path as the owner would type it in a shell: under ~ when it is in the home folder."""
    if file.is_relative_to(home):
        return f"~/{shlex.quote(file.relative_to(home).as_posix())}"
    return shlex.quote(file.as_posix())
