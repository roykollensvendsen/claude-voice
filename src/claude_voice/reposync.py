"""Where each of a profile's repositories stands on this computer, from local refs only.

`claude-voice repos sync --check` reads this: it never fetches and never writes, so it
needs no network. For each folder it says what the acting run would do (clone it, or fetch
and fast-forward its default branch), or why that folder is left alone. A folder is only
fast-forwarded from its own upstream, when it is on its default branch with nothing that
could be lost: no detached HEAD, no operation in progress, no change or untracked file, and
no commit of its own past the upstream's.
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from . import procs, safe_git

if TYPE_CHECKING:
    from .profiles import Repo

# RULE: a repos check takes no git lock
LOCKLESS = ("--no-optional-locks",)  # a plain `git status` writes a refreshed index back
#: What git leaves in its folder while an operation waits for the person to finish it.
OPERATIONS = (
    ("rebase-merge", "rebase"),
    ("rebase-apply", "rebase"),
    ("MERGE_HEAD", "merge"),
    ("CHERRY_PICK_HEAD", "cherry-pick"),
    ("REVERT_HEAD", "revert"),
    ("BISECT_START", "bisect"),
)


@dataclass
class Folder:
    name: str
    url: str
    path: Path
    optional: bool = False
    state: str = "missing"  # or "not a clone", "other origin", "clone"
    origin: str | None = None
    branch: str | None = None  # None when HEAD is detached
    default: str | None = None  # the origin's default branch, as last fetched
    upstream: str | None = None
    ahead: int = 0
    behind: int = 0
    dirty: bool = False
    operation: str | None = None
    hooks: tuple[bool, str | None] = (False, None)  # has a .githooks folder; core.hooksPath

    @property
    def held(self) -> str | None:
        """Why the acting run would leave this clone as it is, or None when it may fast-forward."""
        # RULE: a detached head is left alone
        if self.branch is None:
            return "its HEAD is detached"
        # RULE: a folder mid rebase merge cherry pick or bisect is left alone
        if self.operation:
            return f"a {self.operation} is in progress"
        # RULE: a folder with uncommitted or untracked files is left alone
        if self.dirty:
            return "it has uncommitted or untracked files"
        # RULE: only the default branch is fast forwarded
        if self.branch != self.default:
            return f"it is on {self.branch}, not {self.default or 'a default branch it knows'}"
        if self.upstream is None:
            return f"{self.branch} follows no upstream"
        # RULE: a diverged branch is left alone
        if self.ahead and self.behind:
            return f"{self.branch} has diverged from {self.upstream} ({self.ahead} ahead, {self.behind} behind)"
        return None

    def line(self) -> str:
        """One line for a person: where it is, and what the acting run would do with it."""
        where = f"{self.name}: {self.path}"
        if self.state == "missing":
            return f"{where}: missing; would clone {self.url}"
        if self.state == "not a clone":
            return f"{where}: left alone: it exists and is not a clone; move it away to have {self.url} cloned there"
        if self.state == "other origin":
            return f"{where}: left alone: its origin is {self.origin or 'not set'}, not {self.url}"
        held = self.held
        if held:
            return f"{where}: left alone: {held}"
        known = f"{self.behind} behind as of the last fetch" if self.behind else "level as of the last fetch"
        return f"{where}: {self.branch}, {known}; would fetch, then fast-forward {self.branch}"


def _git(folder: Path, *args: str) -> str | None:
    git = procs.find("git") or "git"
    try:
        out = safe_git.run(
            *LOCKLESS, "-C", str(folder), *args, git=git, capture_output=True, text=True, timeout=10, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() if out.returncode == 0 else None


def _normal(url: str) -> str:
    text = url.strip().lower().rstrip("/").removesuffix(".git")
    text = re.sub(r"^ssh://(?:[^@/]+@)?([^/:]+)(?::\d+)?/", r"https://\1/", text)
    return re.sub(r"^[^@/:]+@([^/:]+):", r"https://\1/", text)


def same_repo(one: str, other: str) -> bool:
    """Whether two remote addresses name one repository, by https or by ssh."""
    return _normal(one) == _normal(other)


def _branches(folder: Folder) -> None:
    path = folder.path
    folder.branch = _git(path, "symbolic-ref", "--short", "-q", "HEAD")
    head = _git(path, "symbolic-ref", "--short", "-q", "refs/remotes/origin/HEAD")
    if head is None:
        head = next(
            (
                f"origin/{b}"
                for b in ("main", "master")
                if _git(path, "rev-parse", "-q", "--verify", f"refs/remotes/origin/{b}")
            ),
            None,
        )
    folder.default = head.removeprefix("origin/") if head else None
    folder.upstream = _git(path, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}")
    counts = _git(path, "rev-list", "--left-right", "--count", "HEAD...@{upstream}") if folder.upstream else None
    if counts:
        folder.ahead, folder.behind = (int(n) for n in counts.split())


def inspect(repo: Repo, path: Path) -> Folder:
    """One folder, as it stands now."""
    folder = Folder(repo.name, repo.url, path, repo.optional)
    if not path.exists():
        return folder
    top = _git(path, "rev-parse", "--show-toplevel")
    # RULE: a folder that is not a clone is never cloned over
    if top is None or Path(top).resolve() != path.resolve():
        folder.state = "not a clone"
        return folder
    folder.origin = _git(path, "remote", "get-url", "origin")
    # RULE: a folder with another origin is only reported
    if folder.origin is None or not same_repo(folder.origin, repo.url):
        folder.state = "other origin"
        return folder
    folder.state = "clone"
    _branches(folder)
    gitdir = Path(_git(path, "rev-parse", "--absolute-git-dir") or path / ".git")
    folder.operation = next((name for mark, name in OPERATIONS if (gitdir / mark).exists()), None)
    folder.dirty = bool(_git(path, "status", "--porcelain", "--untracked-files=all"))
    folder.hooks = ((path / ".githooks").is_dir(), _git(path, "config", "--get", "core.hooksPath"))
    return folder


def check(repos: Iterable[Repo], parent: Path, known: Mapping[str, Path | None]) -> list[Folder]:
    """Every repository, at the folder the settings name for it, or else under `parent`."""
    return [inspect(repo, known.get(repo.name) or parent / repo.name) for repo in repos]
