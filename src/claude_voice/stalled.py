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
import time
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


def same(asked: Asked) -> Asked:
    """`asked` itself: the reading of a question already in hand."""
    return asked


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


OWNERS = re.compile(
    r"push\b[^\n]*(?:--force(?!-with-lease)\b|\s-f\b)"  # a push that forces
    r"|push\b[^\n]*\s(?:origin\s+)?(?:main|master)\b"  # straight onto main
    r"|branch\s+-D\b|push\b[^\n]*\s--delete\b"  # a branch deleted
    r"|plugins\s+approve\b|machines\s+approve\b"  # what only the owner approves
    r"|\blogin\b|/login\b|\bsecret\b|\bpassword\b",  # a sign-in or a secret
    re.I,
)
EVERY = 30.0  # seconds between two looks
NIGHT = (23, 7)  # from this hour until that one, the voice's item waits for the morning


def owners_only(text: str) -> bool:
    """Whether a prompt is one only the owner may answer, so a manager leaves it to him."""
    return bool(OWNERS.search(text))


@dataclass
class Waiting:
    """One prompt a session waits on. `read` finds its question; it is called only once the
    prompt has waited past the limit with the owner away, and once at most."""

    key: str  # the same for as long as it is the same prompt
    session: str
    since: float
    read: Callable[[], Asked | None]
    held: bool = False  # whether the bridge holds it, so it can be answered by voice
    text_ok: bool = True  # whether the question's own words may reach the voice
    manager: bool = True  # whether its programme has a manager to tell


def _local_hour(ts: float) -> int:
    return time.localtime(ts).tm_hour


class Watch:
    """Remembers which prompts were said while the bridge runs, and the voice's held items."""

    def __init__(
        self, after_min: float = 5, clock: Callable[[], float] | None = None, hour: Callable[[float], int] = _local_hour
    ) -> None:
        self.after = after_min * 60
        self.clock = clock or time.time
        self.hour = hour
        self.last: float | None = None
        self.said: set[str] = set()
        self.held: dict[str, str] = {}

    def _lines(self, item: Waiting, minutes: int) -> tuple[str, str]:
        """What the manager hears and what the voice hears."""
        asked = item.read()
        if asked is None:
            line = f"{item.session} has waited {minutes} minutes on a question I can't read; it's in its own window."
            return line, line
        line = f"{item.session} has waited {minutes} minutes on a question: {said_question(asked)}"
        if not item.held:
            line += " It's in its window." + (" Its manager has been told." if item.manager else "")
        # RULE: a prompt only the owner may answer is marked for the manager
        to_manager = f"The owner's: {line}" if owners_only(asked.question) else line
        # RULE: a programme that has not said so hears no question text
        to_voice = line if item.text_ok else f"{item.session} has a question waiting."
        return to_manager, to_voice

    def look(self, waiting: list[Waiting], away: bool) -> tuple[list[tuple[Waiting, str]], list[str]]:
        """What to tell each prompt's manager now, and what to tell the voice now."""
        now = self.clock()
        if self.last is not None and now - self.last < EVERY:
            return [], []
        self.last = now
        present = {w.key for w in waiting}
        self.said &= present  # a prompt that is gone may come back as a new one
        self.held = {k: v for k, v in self.held.items() if k in present}
        managers: list[tuple[Waiting, str]] = []
        for item in waiting:
            # RULE: nothing is said while the owner is at the laptop
            if not away or item.key in self.said or now - item.since < self.after:
                continue
            # RULE: a question waiting past the limit with the owner away is said once
            self.said.add(item.key)
            to_manager, to_voice = self._lines(item, int((now - item.since) // 60))
            managers.append((item, to_manager))
            self.held[item.key] = to_voice
        start, end = NIGHT
        hour = self.hour(now)
        voice: list[str] = []
        # RULE: the voice hears of a question at night only in the morning
        if not (hour >= start or hour < end):
            voice, self.held = list(self.held.values()), {}
        return managers, voice


def said_question(asked: Asked) -> str:
    """The question as it is said: quoted, its own quote marks neutralised, masked, then cut."""
    question = asked.question.translate(QUOTES)
    text = f"'{question}'."
    if asked.options:
        text += " Options: " + ", ".join(f"{i} {o.translate(QUOTES)}" for i, o in enumerate(asked.options, 1)) + "."
    return redact.hide(text, LIMIT)
