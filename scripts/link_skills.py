#!/usr/bin/env python3
"""Make each skill in .claude/skills/ a link to its source in skills/.

Claude Code reads skills from .claude/skills/ and asks the owner before every change
there. The sources live in skills/, where a change is an ordinary edit, and each
folder in .claude/skills/ is a committed relative link to its source. Run it from the
repository root:

    uv run python scripts/link_skills.py          # replace each copy with a link
    uv run python scripts/link_skills.py --check  # change nothing; say what is not a link

A folder is replaced only when its source in skills/ is committed and git holds every
file in the folder as committed. A new, changed or ignored file there is refused and
named, and nothing is changed: a file of yours in .claude/skills/ is never thrown away.
"""

from __future__ import annotations

import argparse
import os
import pathlib
import shutil
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent


def link_text(name: str) -> str:
    """What the link at .claude/skills/<name> holds: relative, so it works in every clone."""
    return f"../../skills/{name}"


def _git(root: pathlib.Path, *args: str) -> list[str]:
    out = subprocess.run(["git", *args], cwd=root, capture_output=True, check=True).stdout.decode("utf-8")
    return [entry for entry in out.split("\0") if entry]


def committed(root: pathlib.Path, name: str) -> bool:
    """Whether git tracks the skill's source in skills/."""
    return bool(_git(root, "ls-files", "-z", "--", f"skills/{name}"))


def unsaved(root: pathlib.Path, name: str) -> list[str]:
    """What in .claude/skills/<name> git does not hold as committed: new, changed or ignored files."""
    entries = _git(
        root, "status", "--porcelain", "-z", "--untracked-files=all", "--ignored", "--", f".claude/skills/{name}"
    )
    return sorted(entry[3:] for entry in entries)


def problems(root: pathlib.Path) -> tuple[list[str], list[str]]:
    """What stops the links, and the skills whose folder is a plain copy ready to replace."""
    sources = sorted(p.parent.name for p in (root / "skills").glob("*/SKILL.md"))
    installed = root / ".claude" / "skills"
    found: list[str] = []
    ready: list[str] = []
    for entry in sorted(installed.iterdir()) if installed.is_dir() else []:
        # RULE: a skill that lives only in .claude is refused
        if entry.name not in sources:
            found.append(f".claude/skills/{entry.name} has no source in skills/; move it there first")
    for name in sources:
        place = installed / name
        if place.is_symlink():
            # RULE: a link that points anywhere but its own source is refused
            if os.readlink(place) != link_text(name):
                found.append(f".claude/skills/{name} links to {os.readlink(place)}, not {link_text(name)}")
            continue
        if not place.exists():
            ready.append(name)
            continue
        if not committed(root, name):
            found.append(f"skills/{name} is not committed yet; commit it first")
            continue
        left = unsaved(root, name)
        # RULE: a skill folder holding anything not committed is refused
        if left:
            found.append(f".claude/skills/{name} holds what is not committed: {', '.join(left)}; commit or move it")
            continue
        ready.append(name)
    return found, ready


def link(root: pathlib.Path, names: list[str]) -> None:
    """Replace each named copy with its link."""
    for name in names:
        place = root / ".claude" / "skills" / name
        if place.exists() and not place.is_symlink():
            shutil.rmtree(place)
        place.parent.mkdir(parents=True, exist_ok=True)
        place.symlink_to(link_text(name), target_is_directory=True)
        print(f"linked .claude/skills/{name} -> {link_text(name)}")


def main(argv: list[str] | None = None, root: pathlib.Path = ROOT) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="change nothing; say what is not a link yet")
    args = parser.parse_args(argv)
    found, ready = problems(root)
    for problem in found:
        print(problem)
    # RULE: nothing is changed while anything is refused
    if found:
        print("Nothing was changed.")
        return 1
    if args.check:
        for name in ready:
            print(f".claude/skills/{name} is not a link yet; run scripts/link_skills.py")
        return 1 if ready else 0
    link(root, ready)
    return 0


if __name__ == "__main__":
    sys.exit(main())
