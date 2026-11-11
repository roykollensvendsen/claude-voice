"""The bridge's environment file: lines a shell or systemd would misread.

The file holds KEY=value lines, the bridge token among them. systemd reads it as
the unit's EnvironmentFile, and shells read it with `set -a; . env`. An unquoted
value with a space sets the first word in a shell and runs the second as a
command, and nothing says so. `problems` finds such lines.

Nothing here ever returns, logs or prints a value: a problem names the line
number and the KEY, and that is all.
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

ENV_FILE = Path("~/.config/claude-voice/env")  # deploy/claude-voice.service's EnvironmentFile

_ASSIGNMENT = re.compile(r"^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$")
# `#` starts a comment only after whitespace, and whitespace is its own problem.
_SPECIAL = re.compile(r"[$`;&|<>()'\"\\]")


@dataclass(frozen=True)
class Problem:
    """One line a shell or systemd would misread; `key` is None when the line has none."""

    line: int
    key: str | None
    what: str
    fix: str

    @property
    def detail(self) -> str:
        """Which line, its KEY, and what is wrong with it."""
        where = f"env line {self.line}" + (f" ({self.key})" if self.key else "")
        return f"{where}: {self.what}"

    def __str__(self) -> str:
        return f"{self.detail}. {self.fix}"


def _closing(value: str, quote: str) -> int:
    """Where the quote that opens `value` closes, or -1 when it never does."""
    i = 1
    while i < len(value):
        if quote == '"' and value[i] == "\\":
            i += 2
            continue
        if value[i] == quote:
            return i
        i += 1
    return -1


def _quoted(number: int, key: str, value: str) -> Problem | None:
    end = _closing(value, value[0])
    if end < 0:
        # RULE: a quote that is never closed is flagged
        return Problem(number, key, "the value opens a quote and never closes it", f'Close it: {key}="…"')
    if value[end + 1 :].strip():
        return Problem(
            number, key, "there is text after the closing quote", f'Keep the whole value inside the quotes: {key}="…"'
        )
    return None


def _unquoted(number: int, key: str, value: str) -> Problem | None:
    if any(c.isspace() for c in value):
        # RULE: an unquoted value with a space is flagged
        return Problem(number, key, "the value has a space and no quotes", f'Put the value in double quotes: {key}="…"')
    if _SPECIAL.search(value):
        # RULE: an unquoted value with a shell character is flagged
        return Problem(
            number,
            key,
            "the value has a character the shell treats specially ($ ` ; & | < > ( ) # quote or backslash) "
            "and no quotes",
            f"Put the value in single quotes: {key}='…'",
        )
    return None


def _line(number: int, raw: str) -> Problem | None:
    text = raw.strip()
    if not text or text.startswith("#"):
        return None
    match = _ASSIGNMENT.match(text)
    if match is None:
        # RULE: a line that is not key equals value is flagged
        return Problem(
            number,
            None,
            "the line is not KEY=value, a comment or blank",
            "Write it as KEY=value, or start it with # to make it a comment",
        )
    key, value = match.group(1), match.group(2)
    if value[:1] in ("'", '"'):
        return _quoted(number, key, value)
    return _unquoted(number, key, value)


def problems(text: str) -> list[Problem]:
    """Every line of an environment file's `text` that a shell or systemd would misread."""
    found = (_line(number, raw) for number, raw in enumerate(text.splitlines(), start=1))
    return [p for p in found if p is not None]


def location(env: Mapping[str, str]) -> Path:
    """The file systemd's EnvironmentFile names, or the one CLAUDE_VOICE_ENV_FILE names."""
    return Path(env.get("CLAUDE_VOICE_ENV_FILE") or ENV_FILE).expanduser()


def read(env: Mapping[str, str]) -> list[Problem]:
    """The problems in the env file; none when it is missing or unreadable."""
    try:
        text = location(env).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    return problems(text)


def warn(log: logging.Logger, env: Mapping[str, str] = os.environ) -> None:
    """Log each problem as one warning line. Never raises: the bridge starts regardless."""
    try:
        found = read(env)
    except Exception:  # a check that fails must not stop the bridge
        log.warning("could not check the env file", exc_info=True)
        return
    for problem in found:
        log.warning("%s", problem)
