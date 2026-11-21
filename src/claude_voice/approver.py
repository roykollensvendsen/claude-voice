"""Which approval boxes the approver session may answer; every other box waits for the owner.

The approver is a session that answers other sessions' routine boxes, so the owner sees
fewer of them (designs/approver-v1). What it may answer is decided here, in code, and
never by its own judgement. It is a short allow-list, and anything else is the owner's:

- the box matched none of the owner's fixed lines (`routing.FIXED`), and its sort is done;
- its input was not cut;
- the tool is Read, Edit or Write on one absolute path inside the session's own repo,
  worktree or scratchpad, with symlinks followed;
- or the tool is Bash running one plain command of literal words: its first word is on a
  short list, no word is a web address or a `user@host:` remote, and every word, read as
  a path from the session's folder, stays inside those folders.

The last rule is why `cat ~/.config/...` or a symlink to a secret never passes: a word is
judged as the file it would name, whatever the program does with it. `uv run`, `make`,
`npm` and `./gradlew` still run the repo's own code; the sandbox is the boundary there.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from . import routing

#: The programs an approvable command may start with (approver-v1 c1).
FIRST_WORDS = frozenset({"uv", "git", "make", "npm", "./gradlew", "ls", "cat", "head", "tail", "grep", "wc"})
FILE_TOOLS = frozenset({"Read", "Edit", "Write"})
#: A web address, or the `user@host:` way of naming another machine.
REMOTE = re.compile(r"://|^[^@/\s]+@[^:/\s]+:")


def why_owner(
    tool: str,
    given: Any,
    lines: Iterable[int] | None,
    place: routing.Place,
    *,
    cwd: Path,
    cut: bool,
) -> str | None:
    """None when the approver may answer this box; otherwise why it is the owner's.

    `lines` are the fixed-list lines the sort found, or None while it has not finished.
    """
    # RULE: a box not yet sorted or cut goes to the owner
    if lines is None:
        return "not sorted yet"
    # RULE: a box with an owner line goes to the owner
    if found := sorted(lines):
        return f"owner line {found[0]}"
    if cut:
        return "the input was cut"
    given = given if isinstance(given, dict) else {}
    roots = [os.path.realpath(root) for root in (place.repo, place.scratchpad) if root is not None]
    if tool in FILE_TOOLS:
        return _file_tool(given.get("file_path"), roots)
    if tool == "Bash":
        return _command(str(given.get("command", "")), roots, cwd)
    # RULE: any other tool goes to the owner
    return f"{tool} is not on the approver's list"


def _file_tool(path: Any, roots: list[str]) -> str | None:
    # RULE: a symlink out of the repo goes to the owner for a file tool
    if not isinstance(path, str) or not os.path.isabs(path) or not routing._inside(path, roots, Path("/")):
        return "a path outside the session's folders"
    return None


def _command(command: str, roots: list[str], cwd: Path) -> str | None:
    parts = routing.split_command(command)
    # RULE: anything but one plain allowed command goes to the owner
    if parts is None or len(parts) != 1 or parts[0][0] not in FIRST_WORDS:
        return "not one plain command from the approver's list"
    # RULE: a command run from outside the repo goes to the owner
    if not routing._inside(str(cwd), roots, Path("/")):
        return "run from outside the session's folders"
    for word in parts[0][1:]:
        # RULE: a command naming a web address or another machine goes to the owner
        if REMOTE.search(word):
            return "names another machine"
        # RULE: a read outside the sessions folders always goes to the owner
        if not all(routing._inside(path, roots, cwd) for path in _as_paths(word)):
            return "a path outside the session's folders"
    return None


def _as_paths(word: str) -> list[str]:
    """Every file `word` could name: itself, a long flag's value, or a short flag's attached value."""
    if word.startswith("--"):
        return [word.split("=", 1)[1]] if "=" in word else []
    if word.startswith("-"):
        return [word[2:]] if len(word) > 2 else []
    return [word]
