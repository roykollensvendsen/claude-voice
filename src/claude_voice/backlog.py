"""A programme's work list: how many of its items wait for a builder.

The manager keeps the list as a Markdown file in the bridge's state folder, in the form the
night queue already uses: each item is a top-level list line, and the lines indented under it
say what became of it, "lane1: taken 23:55", "done: ...", or "blocked: why". An item with
none of those waits; a blocked one is counted apart, because no builder can take it.

The list is read only inside the state folder (docs/org-chart.md, "Idle builders"): a name
that steps out of it, is absolute, or passes a symlink is refused and never opened.
"""

from __future__ import annotations

import errno
import os
import re
from dataclasses import dataclass
from pathlib import Path

from . import safefile

ITEM = re.compile(r"^(?:[-*+]|\d+[.)])\s+\S")  # a list line at the left margin
MARK = re.compile(r"^\s+(?:[\w-]+:\s*)?(taken|done|blocked)\b", re.IGNORECASE)  # an indented line
LARGEST = 1_000_000  # bytes; a list larger than this is read only that far


class Outside(ValueError):
    """A backlog named outside the state folder; the message says what to write instead."""


@dataclass(frozen=True)
class Counts:
    waiting: int = 0
    blocked: int = 0

    def said(self) -> str:
        items = f"{self.waiting} item{'' if self.waiting == 1 else 's'} waiting"
        return f"{items}, {self.blocked} blocked" if self.blocked else items


def count(text: str) -> Counts:
    """How many items in `text` wait, and how many are blocked."""
    marks: list[set[str]] = []
    for line in text.splitlines():
        if ITEM.match(line):
            marks.append(set())
        elif marks and (found := MARK.match(line)):
            marks[-1].add(found.group(1).lower())
    # RULE: an item with no taken or done line under it waits
    open_items = [m for m in marks if not m & {"taken", "done"}]
    # RULE: an item marked blocked counts apart
    blocked = sum(1 for m in open_items if "blocked" in m)
    return Counts(len(open_items) - blocked, blocked)


def path(state: Path, written: str) -> Path:
    """The file `written` names inside `state`; Outside when it would be anywhere else."""
    try:
        # RULE: a backlog is read only inside the state folder
        return safefile.inside(state, written)
    except safefile.Unsafe:
        raise Outside(
            f"backlog {written!r} is outside the state folder {state}; "
            "give a file name inside it, with no '..' and no symlink"
        ) from None


def read(state: Path, written: str) -> Counts:
    """The counts of the list `written` names inside `state`; none when it is not there."""
    target = path(state, written)
    try:
        fd = os.open(target, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except FileNotFoundError:
        return Counts()
    except OSError as exc:
        if exc.errno == errno.ELOOP:  # it became a symlink after it was looked at
            raise Outside(f"backlog {written!r} is a symlink; give a plain file inside {state}") from None
        raise
    with os.fdopen(fd, "rb") as file:
        return count(file.read(LARGEST).decode("utf-8", errors="replace"))
