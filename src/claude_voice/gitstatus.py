"""Where a folder's work stands in git, from local refs only.

Nothing here fetches, so `behind` is only as fresh as the folder's last fetch.
Every question is a plain git command; a folder that is not a repository, or a
computer without git, has no status at all rather than an error.
"""

from __future__ import annotations

import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import procs

CACHE_SECONDS = 45.0
_cache: dict[str, tuple[float, dict[str, Any] | None]] = {}


def _git(folder: Path, *args: str) -> str | None:
    git = procs.find("git")
    if not git:
        return None
    try:
        out = subprocess.run([git, "-C", str(folder), *args], capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() if out.returncode == 0 else None


def _ahead_behind(folder: Path, other: str | None) -> dict[str, Any] | None:
    if not other:
        return None
    counts = _git(folder, "rev-list", "--left-right", "--count", f"HEAD...{other}")
    if not counts:
        return None
    ahead, behind = (int(n) for n in counts.split())
    return {"name": other, "ahead": ahead, "behind": behind}


def _default_branch(folder: Path, remote: str) -> str | None:
    """The remote's main branch: what its HEAD points at, else main, else master."""
    head = _git(folder, "symbolic-ref", "--short", "-q", f"refs/remotes/{remote}/HEAD")
    if head:
        return head
    for name in ("main", "master"):
        if _git(folder, "rev-parse", "-q", "--verify", f"refs/remotes/{remote}/{name}"):
            return f"{remote}/{name}"
    return None


def read(folder: str | Path) -> dict[str, Any] | None:
    """The git status of `folder`, or None when it is not a repository."""
    path = Path(folder)
    if _git(path, "rev-parse", "--is-inside-work-tree") != "true":
        return None
    line = _git(path, "log", "-1", "--format=%h%x00%s") or ""
    hash_, _, subject = line.partition("\x00")
    changed = [x for x in (_git(path, "status", "--porcelain") or "").splitlines() if x.strip()]
    remotes = set((_git(path, "remote") or "").split())
    main = _default_branch(path, "origin") if "origin" in remotes else None
    if main is None:
        main = next(
            (b for b in ("main", "master") if _git(path, "rev-parse", "-q", "--verify", f"refs/heads/{b}")), None
        )
    parent = _default_branch(path, "upstream") if "upstream" in remotes else None
    return {
        "branch": _git(path, "symbolic-ref", "--short", "-q", "HEAD"),
        "commit": {"hash": hash_, "subject": subject} if hash_ else None,
        "dirty": bool(changed),
        "changed_files": len(changed),
        "tracking": _ahead_behind(path, _git(path, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}")),
        "main": _ahead_behind(path, main),
        "parent": _ahead_behind(path, parent),
        "checked_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }


def cached(folder: str | Path) -> dict[str, Any] | None:
    """`read`, asked at most once every CACHE_SECONDS per folder."""
    key = str(folder)
    hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < CACHE_SECONDS:
        return hit[1]
    value = read(folder)
    _cache[key] = (time.monotonic(), value)
    return value
