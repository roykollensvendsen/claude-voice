#!/usr/bin/env python3
"""Remove the merged branches and worktrees the organisation made, and nothing else.

Sessions make a branch and a worktree per job, and once the job is merged they are left
behind. This finds them, says for each what it would do and why, and does it only with
--apply. Run it from anywhere:

    python3 scripts/clean_merged.py [--repo PATH] [--base origin/main] [--apply]

A branch is deleted only when all of these hold:
- its name is one the organisation makes: <type>/... with a conventional-commit type, or
  worktree-..., or it is the branch of a worktree under .claude/worktrees/ that goes too;
- it is not the base, and not on the never-touch list
  (~/.config/claude-voice/never-touch-branches.txt, one name per line, # for comments);
- it has commits of its own, unless it goes with its worktree: a branch with none shows
  nothing of who made it;
- every commit of its own carries a Claude Co-Authored-By line, so none was made by hand.
  That line also marks a branch the owner made with Claude's help; once merged, nothing on
  it is lost, and the never-touch list keeps any the owner wants kept;
- every commit of its own is on the base, by `git cherry`, which sees a rebase merge;
- it is checked out in no worktree that stays.

A worktree is removed only when it lies under <repo>/.claude/worktrees/, its folder exists,
its branch (or its detached commit) is merged as above, `git status` shows nothing
uncommitted, no ignored file in it is anything but a cache a tool makes again (CACHES), and
no running program has its working folder inside it, looked at again just before removal.
Removal uses plain `git worktree remove`, which refuses anything changed but deletes ignored
files; each removal line names the caches it takes. A branch is deleted only if it still
points at the commit that was judged. Only local branches and worktrees are touched, never a branch on GitHub. A missing
never-touch list stops the script before it looks at anything. Standard library only.
"""

from __future__ import annotations

import argparse
import fnmatch
import os
import pathlib
import re
import subprocess
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass

# The never-touch list has one reader, in the package; this loads it from the checkout, so the
# script still runs with a bare python3 and the standard library.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))
from claude_voice.never_touch import NEVER_TOUCH, never_touch  # noqa: E402

TYPES = "feat|fix|docs|chore|ci|test|refactor|perf|build|style|revert"
ORGANISATION_NAME = re.compile(rf"^(?:(?:{TYPES})/.+|worktree-.+)$")
# Ignored folders a tool makes again by itself; any other ignored file keeps its worktree.
CACHES = frozenset(
    {".venv", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", "node_modules", ".hypothesis"}
    | {"build", "dist"}
)
CLAUDE_TRAILER = re.compile(r"^Co-Authored-By: Claude\b", re.MULTILINE | re.IGNORECASE)


@dataclass
class Worktree:
    path: pathlib.Path
    branch: str | None  # None when detached
    head: str
    locked: bool


def _git(repo: pathlib.Path, *args: str) -> str:
    return subprocess.run(  # noqa: S603
        ["git", *args],  # noqa: S607
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def worktrees(repo: pathlib.Path) -> list[Worktree]:
    found: list[Worktree] = []
    for block in _git(repo, "worktree", "list", "--porcelain").strip().split("\n\n"):
        fields = dict((line.split(" ", 1) + [""])[:2] for line in block.splitlines())
        branch = fields.get("branch", "").removeprefix("refs/heads/") or None
        found.append(Worktree(pathlib.Path(fields["worktree"]), branch, fields.get("HEAD", ""), "locked" in fields))
    return found


def session_cwds() -> list[pathlib.Path]:
    """The working folder of every running program this user can see (Linux /proc)."""
    proc = pathlib.Path("/proc")
    if not proc.is_dir():
        raise OSError("no /proc here, so the folders programs work in can't be seen")
    cwds = []
    for entry in proc.iterdir():
        if entry.name.isdigit():
            try:
                cwds.append(pathlib.Path(os.readlink(entry / "cwd")))
            except OSError:
                continue
    return cwds


def not_on_base(repo: pathlib.Path, base: str, ref: str) -> int:
    """How many of `ref`'s own commits have no equal change on `base`."""
    return sum(line.startswith("+") for line in _git(repo, "cherry", base, ref).splitlines())


def made_by_hand(repo: pathlib.Path, base: str, ref: str) -> bool:
    bodies = _git(repo, "log", "--format=%B%x00", f"{base}..{ref}").split("\0")
    return any(body.strip() and not CLAUDE_TRAILER.search(body) for body in bodies)


def _inside(path: pathlib.Path, folder: pathlib.Path) -> bool:
    return path == folder or folder in path.parents


def _plural(n: int) -> str:
    return f"{n} commit{'s' if n != 1 else ''}"


def plan(
    repo: pathlib.Path, base: str, never: set[str], cwds: Iterable[pathlib.Path]
) -> tuple[list[pathlib.Path], list[tuple[str, str]], list[str]]:
    """What to remove: worktrees, branches, and a line per decision."""
    said: list[str] = []
    home = repo / ".claude" / "worktrees"
    base_name = base.split("/", 1)[1] if base.startswith("origin/") else base
    cwds = [p.resolve() for p in cwds]

    def why_kept(branch: str, *, own_worktree: bool) -> str | None:
        if branch in (base_name, "main"):
            return "it is the base"
        # RULE: only a name the organisation makes is cleaned
        if not own_worktree and not ORGANISATION_NAME.match(branch):
            return "not a name the organisation makes"
        # RULE: a branch on the never-touch list is kept
        if branch in never:
            return "on the never-touch list"
        # RULE: a branch with no commits of its own is kept
        if not own_worktree and not int(_git(repo, "rev-list", "--count", f"{base}..{branch}")):
            return "no commits of its own, so nothing shows who made it"
        # RULE: a branch with a commit made by hand is kept
        if made_by_hand(repo, base, branch):
            return "a commit on it was made by hand"
        missing = not_on_base(repo, base, branch)
        # RULE: a branch with a commit not on the base is kept
        if missing:
            return f"{_plural(missing)} not on {base}"
        return None

    gone: list[pathlib.Path] = []
    staying: set[str] = set()
    for tree in worktrees(repo):
        if tree.path.resolve() == repo.resolve():
            if tree.branch:
                staying.add(tree.branch)
            continue
        reason = None
        if not _inside(tree.path.resolve(), home.resolve()):
            reason = "not under .claude/worktrees"
        # RULE: a worktree whose folder is gone is named and skipped
        elif not tree.path.is_dir():
            reason = "its folder is gone; run git worktree prune"
        elif tree.locked:
            reason = "it is locked"
        elif tree.branch:
            reason = why_kept(tree.branch, own_worktree=True)
        elif not_on_base(repo, base, tree.head):
            reason = f"its commit is not on {base}"
        # RULE: a worktree holding work not committed is kept
        if reason is None and _git(tree.path, "status", "--porcelain", "--untracked-files=all").strip():
            reason = "it holds work not committed"
        # RULE: a worktree holding ignored files that are not caches is kept
        if reason is None and (precious := [p for p in ignored(tree.path) if not is_cache(p)]):
            reason = f"it holds ignored files that are not caches: {', '.join(precious)}"
        # RULE: a worktree a program is working in is kept
        if reason is None and any(_inside(cwd, tree.path.resolve()) for cwd in cwds):
            reason = "a program is working in it"
        if reason:
            said.append(f"keep worktree {tree.path}: {reason}")
            if tree.branch:
                staying.add(tree.branch)
        else:
            gone.append(tree.path)
    going_with_tree = {t.branch for t in worktrees(repo) if t.path in gone and t.branch}

    branches: list[tuple[str, str]] = []
    for line in _git(repo, "branch", "--format=%(refname:short) %(objectname)").splitlines():
        branch, judged = line.split()
        if branch in staying:
            if ORGANISATION_NAME.match(branch):
                said.append(f"keep branch {branch}: checked out in a worktree that stays")
            continue
        reason = why_kept(branch, own_worktree=branch in going_with_tree)
        if reason:
            if ORGANISATION_NAME.match(branch) or reason == "not a name the organisation makes":
                said.append(f"keep branch {branch}: {reason}")
        else:
            branches.append((branch, judged))
    return gone, branches, said


def main(argv: list[str] | None = None, cwds: Callable[[], Iterable[pathlib.Path]] = session_cwds) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", type=pathlib.Path, default=pathlib.Path(__file__).resolve().parent.parent)
    parser.add_argument("--base", default="origin/main")
    parser.add_argument("--never-touch", type=pathlib.Path, default=NEVER_TOUCH)
    parser.add_argument("--apply", action="store_true", help="remove; without it, only say what would go")
    args = parser.parse_args(argv)
    # RULE: a missing never-touch list stops everything
    if not args.never_touch.is_file():
        print(f"stop: {args.never_touch} is missing. Write the owner's own branches there, one per line.")
        return 2
    try:
        working = list(cwds())
    except OSError as exc:
        print(f"stop: {exc}")
        return 2
    gone, branches, said = plan(args.repo.resolve(), args.base, never_touch(args.never_touch), working)
    for line in said:
        print(line)
    verb = "" if args.apply else "would "
    merged = f"merged: every change on it is on {args.base}, nothing uncommitted, no program in it"
    entered: set[pathlib.Path] = set()
    for path in gone:
        # RULE: a worktree a program started working in is kept
        if args.apply and any(_inside(cwd.resolve(), path.resolve()) for cwd in cwds()):
            print(f"keep worktree {path}: a program started working in it")
            entered.add(path)
            continue
        caches = ignored(path)
        print(f"{verb}remove worktree {path}: {merged}" + (f"; takes ignored {', '.join(caches)}" if caches else ""))
        if args.apply:
            _git(args.repo, "worktree", "remove", str(path))
    held = {tree.branch for tree in worktrees(args.repo) if tree.path in entered}
    for branch, judged in branches:
        if branch in held:
            print(f"keep branch {branch}: checked out in a worktree that stays")
            continue
        print(f"{verb}delete branch {branch} at {judged[:12]}: every change on it is on {args.base}")
        if args.apply:
            delete_branch(args.repo, branch, judged)
    return 0


def ignored(path: pathlib.Path) -> list[str]:
    """The ignored files and folders in a worktree, which `git worktree remove` deletes without asking."""
    lines = _git(path, "status", "--porcelain", "--ignored=matching").splitlines()
    return [line[3:] for line in lines if line.startswith("!! ")]


def is_cache(entry: str) -> bool:
    """Whether an ignored entry is something a tool makes again by itself."""
    name = pathlib.PurePosixPath(entry.rstrip("/")).name
    return name in CACHES or fnmatch.fnmatch(name, "*.egg-info") or fnmatch.fnmatch(name, ".coverage*")


def delete_branch(repo: pathlib.Path, branch: str, judged: str) -> None:
    """Delete `branch` only if it still points at `judged`, the commit the checks looked at.

    `git branch -d` can't be the backstop: a rebase merge leaves the branch off main's history,
    so -d refuses every merged branch. This refuses instead if the branch moved since."""
    # RULE: a branch that moved since it was judged is not deleted
    _git(repo, "update-ref", "-d", f"refs/heads/{branch}", judged)


if __name__ == "__main__":
    sys.exit(main())
