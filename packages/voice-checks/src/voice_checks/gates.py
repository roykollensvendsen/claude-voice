"""The gate files: what a pull request may not change and still be merged by a session.

github-rights-v1, §4 point 4 and R4. The list is ``gate_files.toml`` beside this module. A repo
adds to it in its pyproject.toml and never takes from it. A pull request is judged by the
union of the list as it was on the base and as it is on the head, so dropping a line is caught
by the line as it was. A mutation row deleted, or with its find or replace changed, counts too,
wherever its file lives.
"""

from __future__ import annotations

import pathlib
import re
import subprocess
import tomllib
from collections.abc import Iterable

LIST = pathlib.Path(__file__).with_name("gate_files.toml")
OWNER_MERGES = "the owner merges this one (github-rights-v1 Q2)"


class GateError(Exception):
    """A gate list that cannot be read, said as what to do instead."""


def _regex(glob: str) -> re.Pattern[str]:
    """A glob as a whole-path pattern: `*` within one folder, `**` across any number, or none."""
    out, i = [], 0
    while i < len(glob):
        if glob.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif glob.startswith("**", i):
            out.append(".*")
            i += 2
        elif glob[i] == "*":
            # RULE: a star never crosses a folder
            out.append("[^/]*")
            i += 1
        else:
            out.append(re.escape(glob[i]))
            i += 1
    return re.compile("".join(out))


def gate(path: str, globs: Iterable[str]) -> bool:
    """Whether a repo-relative path is one of the gates."""
    return any(_regex(glob).fullmatch(path) for glob in globs)


def _adds(pyproject: str) -> list[str]:
    """The paths a repo adds in its pyproject.toml; any other key is refused."""
    if not pyproject.strip():
        return []
    table = tomllib.loads(pyproject).get("tool", {}).get("voice-checks", {}).get("gate-files", {})
    others = sorted(set(table) - {"add"})
    # RULE: a repo cannot name a gate to remove
    if others:
        msg = f"[tool.voice-checks.gate-files] may only add paths; it also says {', '.join(others)}"
        raise GateError(msg)
    return [str(p) for p in table.get("add", [])]


def paths_from(lists: Iterable[str], pyprojects: Iterable[str], also: Iterable[str] = ()) -> list[str]:
    """Every gate the given lists and pyprojects name, with `also` added: a union, never less."""
    found = set(also)
    for text in lists:
        if text.strip():
            found |= {str(p) for p in tomllib.loads(text).get("gates", {}).get("paths", [])}
    for text in pyprojects:
        found |= set(_adds(text))
    return sorted(found)


def paths(root: pathlib.Path | None = None) -> list[str]:
    """The shared list, with what the repo at `root` adds in its pyproject.toml."""
    pyproject = root / "pyproject.toml" if root is not None else None
    added = [pyproject.read_text(encoding="utf-8")] if pyproject is not None and pyproject.is_file() else []
    return paths_from([LIST.read_text(encoding="utf-8")], added)


def _rules(texts: Iterable[str]) -> tuple[list[dict[str, str]], bool]:
    """Every mutation row in the texts, and whether any text was not valid TOML."""
    rows, broken = [], False
    for text in texts:
        try:
            rows += [dict(r) for r in tomllib.loads(text).get("rule", [])]
        except tomllib.TOMLDecodeError:
            broken = True
    return rows, broken


def row_changes(before: Iterable[str], after: Iterable[str]) -> list[str]:
    """How a pull request weakens a mutation row: deleted, or its find or replace changed.

    `before` and `after` are its changed .toml files as they were and as they are, so a row whose
    file moves is still found. A row kept by name, or kept by its find and replace under a new
    name, is unchanged; a row added is never a weakening.
    """
    was, _ = _rules(before)
    now, broken = _rules(after)
    if was and broken:
        return ["makes a row file that is not valid TOML"]
    named = {r.get("name"): _what(r) for r in now}
    kept = set(named.values())
    found = []
    for row in was:
        name = row.get("name")
        if name in named:
            # RULE: a row whose find or replace changes is a gate change
            if named[name] != _what(row):
                found.append(f"changes the row {name!r}")
        # RULE: a row deleted, and kept under no name, is a gate change
        elif _what(row) not in kept:
            found.append(f"deletes the row {name!r}")
    return sorted(found)


#: Where the list sits in claude-voice's own tree, so a pull request there is judged by the list
#: as it was on the base too. A plugin has no copy in its tree; it gets the list by its pin, which
#: is itself a gate.
IN_TREE = "packages/voice-checks/src/voice_checks/gate_files.toml"


def _git(root: pathlib.Path, *args: str) -> str:
    try:
        done = subprocess.run(  # noqa: S603 - a fixed command, no shell
            ["git", *args], cwd=root, capture_output=True, text=True, check=True, timeout=60
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as failed:
        msg = f"git {args[0]} could not run, so nothing was judged ({failed})"
        # RULE: a base that cannot be read fails closed
        raise GateError(msg) from failed
    return done.stdout


def _at(root: pathlib.Path, ref: str, path: str) -> str:
    """A file's text at a commit, or nothing where it is not there."""
    listed = _git(root, "ls-tree", "--name-only", ref, "--", path)
    return _git(root, "show", f"{ref}:{path}") if listed.strip() else ""


def judge(root: pathlib.Path, base: str) -> list[str]:
    """Every way the changes since `base` touch a gate: by path, and by mutation row."""
    since = _git(root, "merge-base", base, "HEAD").strip()
    # RULE: a gate file with a name git would quote still fails and is named
    fields = _git(root, "diff", "-z", "--no-renames", "--name-status", since, "HEAD").split("\0")[:-1]
    changed = list(zip(fields[::2], fields[1::2], strict=True))  # -z: status, path, each as git has it
    head = {path: _at(root, "HEAD", path) for status, path in changed if status != "D"}
    was = {path: _at(root, since, path) for status, path in changed if status != "A"}
    judges = paths_from(
        [LIST.read_text(encoding="utf-8"), _at(root, since, IN_TREE), _at(root, "HEAD", IN_TREE)],
        # RULE: a path the base added is still a gate when the head drops it
        [_at(root, since, "pyproject.toml"), _at(root, "HEAD", "pyproject.toml")],
    )
    # RULE: a pull request that touches a gate file fails and names it
    found = [f"changes {path}, a gate file" for _, path in changed if gate(path, judges)]
    toml = [p for _, p in changed if p.endswith(".toml")]
    found += row_changes([was.get(p, "") for p in toml], [head.get(p, "") for p in toml])
    return found


def _what(row: dict[str, str]) -> tuple[str | None, str | None]:
    """What a row does: the line it finds, and what it puts there."""
    return row.get("find"), row.get("replace")
