"""Values only the owner can give, typed into a terminal without being shown.

A plugin's manifest names each one (docs/plugin-manifest.md): what kind of value it is and
what fits. The home position is two of them. An answer that does not fit is asked again,
naming what fits and never the answer, so a mistyped value is not shown either.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

TYPES = ("float", "int", "str", "choice")
NOT_A_TERMINAL = "{verb} asks you questions; run it in a terminal of your own, not from Claude Code or through a pipe"


class Stream(Protocol):
    def isatty(self) -> bool: ...


def in_terminal(env: Mapping[str, str], stdin: Stream, stdout: Stream) -> bool:
    """Whether a person is at a terminal to answer: both ends are one, and it is not a
    Claude Code session. That last is a slip guard only; the typed approval is the gate."""
    # RULE: questions are asked only in a real terminal
    return stdin.isatty() and stdout.isatty() and not env.get("CLAUDECODE")


class Refused(ValueError):
    """An answer that does not fit. Its message says what fits, never what was typed."""


@dataclass(frozen=True)
class Field:
    """One kind of answer and what fits it."""

    type: str
    range: tuple[float, float] | None = None
    round: int | None = None  # decimals a float keeps
    max_len: int | None = None  # for str
    values: tuple[str, ...] = ()  # for choice

    def fits(self) -> str:
        """What fits, in words."""
        if self.type == "choice":
            return "one of: " + ", ".join(self.values)
        if self.type == "str":
            return f"text of at most {self.max_len} characters"
        kind = "a whole number" if self.type == "int" else "a number"
        if self.range is None:
            return kind
        return f"{kind} from {self.range[0]:g} to {self.range[1]:g}"

    def parse(self, typed: str) -> Any:
        """The answer as its value, or Refused."""
        text = typed.strip()
        if self.type == "choice":
            if text not in self.values:
                raise Refused(self.fits())
            return text
        if self.type == "str":
            if not text or len(text) > (self.max_len or 0):
                raise Refused(self.fits())
            return text
        return self._number(text)

    def _number(self, text: str) -> float | int:
        try:
            value: float | int = int(text) if self.type == "int" else float(text.replace(",", "."))
        except ValueError:
            raise Refused(self.fits()) from None
        if not math.isfinite(value):
            raise Refused(self.fits())
        if self.round is not None:
            value = round(value, self.round)
        # RULE: an answer outside its range is refused
        if self.range is not None and not self.range[0] <= value <= self.range[1]:
            raise Refused(self.fits())
        return value


def ask(field: Field, prompt: str, read: Callable[[str], str], say: Callable[[str], None]) -> Any:
    """Ask until the answer fits. EOFError and KeyboardInterrupt from `read` pass through."""
    while True:
        try:
            return field.parse(read(prompt))
        except Refused as exc:
            # RULE: a refused answer is asked again naming what fits
            say(f"   That does not fit: it must be {exc}. Try again.")
