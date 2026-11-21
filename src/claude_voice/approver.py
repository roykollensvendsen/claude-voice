"""Which approval boxes the approver session may answer; every other box waits for the owner.

The approver is a session that answers other sessions' routine boxes, so the owner sees
fewer of them (designs/approver-v1). What it may answer is decided here, in code, and
never by its own judgement. It is a short allow-list, and anything else is the owner's:

- the box matched none of the owner's fixed lines (`routing.FIXED`), and its sort is done;
- its input was not cut;
- the tool is Read on one absolute path inside the session's own repo, worktree or
  scratchpad, with symlinks followed;
- or Edit or Write on such a path inside the repo, but never under `.git`,
  `.github/workflows` or any `.claude` folder, since those run later as the owner;
- or the tool is Bash running one plain command of literal words, from the session's
  folder. It starts with a reader (`ls`, `cat`, `head`, `tail`, `grep`, `wc`) or with a
  listed subcommand of `git`, `uv` or `npm`, or with `make`. No word holds a `:`, so no
  web address or other machine. Every word, and every value glued to a flag, read as a
  path from the session's folder, stays inside those folders.

The last rule is why `cat ~/.config/...` or a symlink to a secret never passes: a word is
judged as the file it would name, whatever the program does with it. `uv run`, `make` and
`npm` still run the repo's own code; the sandbox is the boundary there.
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from . import routing

READERS = frozenset({"ls", "cat", "head", "tail", "grep", "wc"})
#: The subcommands each program may run (approver-v1 v1.3); `make` takes any target.
SUBCOMMANDS = {
    "git": routing.GIT_SHAPES,
    "uv": frozenset({"run", "sync"}),
    "npm": frozenset({"test", "run", "ci"}),
}
#: Options that make uv fetch and run code from outside the repo.
UV_FETCHES = ("--with", "--from", "--index", "--extra-index-url", "--find-links")
#: Options that make git run a command given on the line or set its config.
GIT_RUNS = ("-c", "-x", "--exec")
FIRST_WORDS = READERS | SUBCOMMANDS.keys() | {"make"}


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
    if tool in ("Read", "Edit", "Write"):
        return _file_tool(tool, given.get("file_path"), roots, place)
    if tool == "Bash":
        return _command(str(given.get("command", "")), roots, cwd)
    # RULE: any other tool goes to the owner
    return f"{tool} is not on the approver's list"


def _file_tool(tool: str, path: Any, roots: list[str], place: routing.Place) -> str | None:
    # RULE: a symlink out of the repo goes to the owner for a file tool
    if not isinstance(path, str) or not os.path.isabs(path) or not routing.inside(path, roots, Path("/")):
        return "a path outside the session's folders"
    # RULE: an edit of git, workflow or claude files goes to the owner
    if tool != "Read" and not _writable(path, place):
        return "a file that runs later as the owner"
    return None


def _writable(path: str, place: routing.Place) -> bool:
    """Inside the repo, and not under .git, .github/workflows or any .claude folder."""
    rel = Path(os.path.relpath(os.path.realpath(path), os.path.realpath(place.repo)))
    return routing._editable(path, place) and ".claude" not in rel.parts


def _command(command: str, roots: list[str], cwd: Path) -> str | None:
    parts = routing.split_command(command)
    # RULE: anything but one plain allowed command goes to the owner
    if parts is None or len(parts) != 1 or parts[0][0] not in FIRST_WORDS:
        return "not one plain command from the approver's list"
    if why := _program(parts[0][0], parts[0][1:]):
        return why
    # RULE: a command run from outside the repo goes to the owner
    if not routing.inside(str(cwd), roots, Path("/")):
        return "run from outside the session's folders"
    for word in parts[0][1:]:
        # RULE: a command naming a web address or another machine goes to the owner
        if ":" in word:
            return "names another machine"
        # RULE: a read outside the sessions folders always goes to the owner
        if not all(routing.inside(path, roots, cwd) for path in _as_paths(word)):
            return "a path outside the session's folders"
    return None


def _program(program: str, rest: list[str]) -> str | None:
    """Why this program's own words are the owner's, or None."""
    # RULE: a subcommand off the approvers list goes to the owner
    if program in SUBCOMMANDS and (not rest or rest[0] not in SUBCOMMANDS[program]):
        return f"{' '.join([program, *rest[:1]])} is not on the approver's list"
    # RULE: git that reaches a named remote or runs a command goes to the owner
    if program == "git" and ((rest[0] == "fetch" and len(rest) > 1) or any(w.startswith(GIT_RUNS) for w in rest)):
        return "git that reaches a named remote or runs a command"
    # RULE: uv run that fetches other code goes to the owner
    if program == "uv" and any(w.startswith(UV_FETCHES) for w in rest):
        return "uv that fetches code from outside the repo"
    # RULE: make that sets a variable goes to the owner
    if program == "make" and any("=" in w for w in rest):
        return "make that sets a variable"
    return None


def _as_paths(word: str) -> list[str]:
    """Every file `word` could name: itself, a long flag's value, or any tail of a short-flag
    cluster, since its value may follow any of its letters (`-rf..`, `-rf/etc/x`, `-C..`)."""
    if word.startswith("--"):
        return [word.split("=", 1)[1]] if "=" in word else []
    if word.startswith("-"):
        # RULE: a value glued to a short flag is judged as a path
        return [word[i:] for i in range(2, len(word))]
    return [word]
