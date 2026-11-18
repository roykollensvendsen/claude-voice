"""A session waiting on a question while nobody is at the laptop: noticed, and said once.

A permission prompt or a choice box can wait for hours when the owner is away. When one has
waited past the limit and nobody is at the laptop, the voice hears it once, and so does the
manager of the session's programme (docs/event-api.md, "A question left waiting").

The question comes from the prompt the bridge holds, or else is read off the session's tmux
pane. A pane is only ever read here, never typed into: answering stays with the owner and
the manager, through the paths they already have. Everything said is masked and cut first.
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field

from . import redact

LIMIT = 200  # characters of a question that are sent, after masking
BORDERS = "│╭╮╰╯┃"
RULE_LINE = re.compile(r"^\s*[╭─━]{3,}")  # a box's top edge, or the rule above an open prompt
BOTTOM = re.compile(r"^\s*╰")
OPTION = re.compile(r"^(?:[❯›>]\s*)?(\d+)\.\s+(.*)$")
QUOTES = str.maketrans({'"': "ʼ", "'": "ʼ", "“": "ʼ", "”": "ʼ", "‘": "ʼ", "’": "ʼ", "`": "ʼ"})


@dataclass(frozen=True)
class Asked:
    """A question as a session shows it, and its options in order."""

    question: str
    options: list[str] = field(default_factory=list)


def read_pane(pane: str, run: Callable[[list[str]], str]) -> str:
    """The text of tmux pane `pane`, wrapped lines joined. Only read: nothing is typed."""
    # RULE: a pane is read with its wrapped lines joined
    return run(["tmux", "capture-pane", "-p", "-J", "-t", pane])


def run_tmux(command: list[str]) -> str:
    return subprocess.run(command, check=True, capture_output=True, text=True, timeout=5).stdout


def _content(line: str) -> tuple[str, bool]:
    """A prompt line without its box's borders, and whether it ended before the box's edge, so
    the next line starts a new word rather than carrying on a broken one."""
    boxed = line.rstrip().endswith(tuple(BORDERS))
    inner = line.rstrip().rstrip(BORDERS) if boxed else line.rstrip()
    inner = inner.lstrip().lstrip(BORDERS)
    return inner, (not boxed) or inner.endswith(" ") or not inner


def _join(parts: list[tuple[str, bool]]) -> str:
    out = ""
    ended = True
    for text, ends in parts:
        text = text.strip()
        if text:
            # RULE: a line broken at the edge of its box is joined without a space
            out += (" " if out and ended else "") + text
            ended = ends
    return out


def _block(lines: list[str]) -> list[str]:
    """The lines of the last prompt on the pane: after its top edge, before its bottom."""
    end = next((i for i in range(len(lines) - 1, -1, -1) if BOTTOM.match(lines[i])), len(lines))
    start = next((i for i in range(end - 1, -1, -1) if RULE_LINE.match(lines[i])), -1)
    return lines[start + 1 : end]


def parse_pane(text: str) -> Asked | None:
    """The question and numbered options of the last prompt on a pane; None when it shows none."""
    question: list[tuple[str, bool]] = []
    options: list[list[tuple[str, bool]]] = []
    for line in _block(text.splitlines()):
        inner, ends = _content(line)
        found = OPTION.match(inner.strip())
        if found:
            options.append([(found.group(2), ends)])
        elif options:
            options[-1].append((inner, ends))
        else:
            question.append((inner, ends))
    if not options:
        return None
    return Asked(_join(question), [_join(o) for o in options])


def said_question(asked: Asked) -> str:
    """The question as it is said: quoted, its own quote marks neutralised, masked, then cut."""
    question = asked.question.translate(QUOTES)
    text = f"'{question}'."
    if asked.options:
        text += " Options: " + ", ".join(f"{i} {o.translate(QUOTES)}" for i, o in enumerate(asked.options, 1)) + "."
    return redact.hide(text, LIMIT)
