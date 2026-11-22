"""The setup guide is one an agent can follow, and the README's list of steps is made from it.

``docs/setup.md`` is the one source. It is written for an agent, top to bottom:

- each step is a heading ``### <number>. <what it does>``, with a fenced command block and a
  line starting ``Check:`` that names the command whose output proves the step worked;
- a block runs in the tests only when the line above its fence is ``<!-- safe-to-run -->``;
- the steps under ``## Steps only the owner can do`` are marked ``<!-- owner -->``, and each
  says ``Why:`` only the owner can do it, or the heading says ``None.``.

The README, for people, holds between ``<!-- steps:begin -->`` and ``<!-- steps:end -->`` the
first line of each agent step and every owner step in full. ``voice-checks docs --write`` writes
that block, and ``voice-checks docs`` checks it and the guide.

Moved from messages-mcp's and ams-mcp's scripts/docs.py and tests/test_docs.py, which were the
same (no-duplicates-v1, note n12). Running the safe blocks stays in each plugin's tests, with
its own bounded run; this gives them the blocks and the environment to run them in.
"""

from __future__ import annotations

import dataclasses
import os
import pathlib
import re

SETUP, README = "docs/setup.md", "README.md"
BEGIN, END = "<!-- steps:begin -->", "<!-- steps:end -->"
OWNER_HEADING = "## Steps only the owner can do"
OWNER, SAFE = "<!-- owner -->", "<!-- safe-to-run -->"
STEP = re.compile(r"^### (\w+)\. (.+?)\s*$")
FIX = "run `uv run voice-checks docs --write` and commit the README"
#: The only variables a safe step is given from the test's own environment. Anything else,
#: such as a token or a key, never reaches a command the guide shows.
PASSED_ON = ("PATH", "LANG", "LC_ALL", "UV_CACHE_DIR", "UV_PYTHON_INSTALL_DIR")
#: The folders a safe step could write in, each moved into the test's own folder.
MOVED = ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME", "TMPDIR")


class DocsError(Exception):
    """A README the block cannot be written into, said as what to do instead."""


@dataclasses.dataclass(frozen=True)
class Block:
    """One fenced command block in a step."""

    code: str
    safe: bool


@dataclasses.dataclass(frozen=True)
class Step:
    """One step of the guide, as it is written."""

    number: str
    title: str
    lines: tuple[str, ...]
    in_owner_section: bool

    @property
    def owner(self) -> bool:
        """Marked as a step only the owner can do."""
        return OWNER in (line.strip() for line in self.lines)

    @property
    def check(self) -> str:
        """What follows ``Check:``, or nothing when the step has no check."""
        for line in self.lines:
            if line.startswith("Check:"):
                return line.removeprefix("Check:").strip()
        return ""

    @property
    def why(self) -> str:
        """What follows ``Why:``, the reason only the owner can do it."""
        for line in self.lines:
            if line.startswith("Why:"):
                return line.removeprefix("Why:").strip()
        return ""

    @property
    def blocks(self) -> list[Block]:
        """The step's fenced blocks, each marked safe to run or not."""
        found: list[Block] = []
        inside, code, safe = False, list[str](), False
        for i, line in enumerate(self.lines):
            if line.startswith("```") and not inside:
                # RULE: only a block marked safe to run is ever run
                inside, code, safe = True, [], i > 0 and self.lines[i - 1].strip() == SAFE
            elif line.startswith("```"):
                inside = False
                found.append(Block("\n".join(code), safe))
            elif inside:
                code.append(line)
        return found


def steps(guide: str) -> list[Step]:
    """Every step in the guide, in order."""
    found: list[Step] = []
    number: str | None = None
    title: str | None = None
    body: list[str] = []
    owner_section = False
    fenced = False

    def close() -> None:
        if number is not None and title is not None:
            found.append(Step(number, title, tuple(_trimmed(body)), owner_section))

    for line in guide.splitlines():
        if line.startswith("```"):
            fenced = not fenced
        # RULE: a heading inside a command block is not a step
        if fenced or line.startswith("```"):
            if number is not None:
                body.append(line)
            continue
        heading = STEP.match(line)
        if heading or line.startswith(("## ", "# ")):
            close()
            number = title = None
            body = []
        if line.strip() == OWNER_HEADING:
            owner_section = True
        elif line.startswith("## "):
            owner_section = False
        if heading:
            number, title = heading.groups()
        elif number is not None:
            body.append(line)
    close()
    return found


def _trimmed(lines: list[str]) -> list[str]:
    while lines and not lines[0].strip():
        lines = lines[1:]
    while lines and not lines[-1].strip():
        lines = lines[:-1]
    return lines


def block(guide: str) -> str:
    """The README's step list, as made from the guide now, markers included."""
    agent = [s for s in steps(guide) if not s.owner]
    owner = [s for s in steps(guide) if s.owner]
    out = [
        BEGIN,
        "<!-- Made by `voice-checks docs --write` from docs/setup.md. Change the guide, then run it. -->",
        "",
        "The steps, in full, are in [docs/setup.md](docs/setup.md):",
        "",
    ]
    out += [f"{s.number}. {s.title}" for s in agent]
    out += ["", "### Steps only the owner can do", ""]
    if not owner:
        out += ["None.", ""]
    for s in owner:
        shown = [line for line in s.lines if line.strip() not in (OWNER, SAFE)]
        out += [f"#### {s.number}. {s.title}", "", *shown, ""]
    out.append(END)
    return "\n".join(out)


def updated(readme: str, guide: str) -> str:
    """The README with its block replaced by the one the guide makes now."""
    if BEGIN not in readme or END not in readme:
        msg = f"README.md has no {BEGIN} … {END} block; add the two markers, then {FIX}"
        raise DocsError(msg)
    head, rest = readme.split(BEGIN, 1)
    tail = rest.split(END, 1)[1]
    return head + block(guide) + tail


def safe_blocks(guide: str) -> list[tuple[str, str]]:
    """Each block marked safe to run, by the number of its step: what a plugin's tests run."""
    return [(s.number, b.code) for s in steps(guide) for b in s.blocks if b.safe]


def isolated_env(folder: pathlib.Path) -> dict[str, str]:
    """The environment a safe step runs in: every folder it could write lies in `folder`.

    One folder is shared on purpose: uv's cache, so a step installs from it with no network.
    """
    env = {k: os.environ[k] for k in PASSED_ON if k in os.environ}
    env["HOME"] = str(folder / "home")
    pathlib.Path(env["HOME"]).mkdir()
    for name in MOVED:
        env[name] = str(folder / name.lower())
        pathlib.Path(env[name]).mkdir()
    # uv's cache stays where it was, so a safe step needs no network.
    env.setdefault("UV_CACHE_DIR", os.environ.get("UV_CACHE_DIR", str(pathlib.Path.home() / ".cache/uv")))
    return env


def _guide_problems(guide: str) -> list[str]:
    """What stops an agent following the guide, step by step."""
    found = steps(guide)
    if not found:
        return [f"{SETUP} has no steps"]
    problems = [f"step {s.number} has no Check: line" for s in found if not s.check]
    problems += [f"step {s.number} shows no command" for s in found if not s.owner and not s.blocks]
    if f"\n{OWNER_HEADING}\n" not in f"\n{guide}":
        return [*problems, f"{SETUP} has no '{OWNER_HEADING}' heading"]
    under = [s for s in found if s.in_owner_section]
    # RULE: the owner part starts at the owner heading itself
    owner_part = f"\n{guide}".split(f"\n{OWNER_HEADING}\n", 1)[1].split("\n## ", 1)[0]
    # RULE: a guide whose owner part says none has nothing to say
    if not under and owner_part.strip() != "None.":
        problems.append(f"'{OWNER_HEADING}' holds no step and does not say None.")
    problems += [f"step {s.number} is not marked {OWNER}" for s in under if not s.owner]
    problems += [f"step {s.number} does not say Why: only the owner can do it" for s in under if not s.why]
    problems += [
        f"step {s.number} is marked {OWNER} but is not under '{OWNER_HEADING}'"
        for s in found
        if s.owner and not s.in_owner_section
    ]
    return problems


def _missing(root: pathlib.Path) -> list[str]:
    # RULE: a missing guide or readme is named not a traceback
    return [f"{name} is missing" for name in (SETUP, README) if not (root / name).is_file()]


def problems(root: pathlib.Path) -> list[str]:
    """Everything wrong with a plugin's guide and the README block made from it."""
    if gone := _missing(root):
        return gone
    guide = (root / SETUP).read_text(encoding="utf-8")
    readme = (root / README).read_text(encoding="utf-8")
    found = _guide_problems(guide)
    try:
        fresh = updated(readme, guide)
    except DocsError as refused:
        return [*found, str(refused)]
    # RULE: a stale readme is refused and says how to fix it
    if fresh != readme:
        found.append(f"README.md's step list is not what {SETUP} says now; {FIX}")
    return found


def write(root: pathlib.Path) -> None:
    """Write the README's block from the guide, with the same line ends on every system."""
    if gone := _missing(root):
        raise DocsError("; ".join(gone))
    readme = root / README
    guide = (root / SETUP).read_text(encoding="utf-8")
    readme.write_text(updated(readme.read_text(encoding="utf-8"), guide), encoding="utf-8", newline="\n")
