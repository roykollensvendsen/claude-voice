"""Developer mode, and the one-time questions the owner answers by voice.

Developer mode is the window in which the owner's voice may approve a plugin whose tools
changed (ADR-CV-010). It is off by default, switched on by voice with a question and a
spoken yes, switched off at once from anywhere, and ends by itself after eight hours, so a
forgotten window does not stay open. Its state is a small file, so a restart of the bridge
keeps it. Every switch is logged.

A question works the way the garage door's does: a request returns a token with the
question, and only the matching confirm, within two minutes and once, acts on it.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

log = logging.getLogger("claude_voice")
STATE = Path("~/.local/state/claude-voice/developer-mode.json")
HOURS = 8
QUESTION_SECONDS = 120.0


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, UTC).isoformat(timespec="seconds")


class DeveloperMode:
    """Whether developer mode is on, and until when."""

    def __init__(self, path: Path | None = None, clock: Callable[[], float] = time.time) -> None:
        self.path = (path or STATE).expanduser()
        self.clock = clock

    def _until(self) -> float | None:
        try:
            until = json.loads(self.path.read_text()).get("until")
        except (OSError, ValueError, AttributeError):
            return None
        return float(until) if isinstance(until, int | float) else None

    def _write(self, until: float | None) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        spare = self.path.with_name(self.path.name + ".tmp")
        spare.write_text(json.dumps({"until": until}) + "\n")
        os.replace(spare, self.path)

    def state(self) -> dict[str, Any]:
        """{"on": bool, "until": when it ends, or None}."""
        until = self._until()
        # RULE: developer mode ends by itself after eight hours
        if until is not None and until <= self.clock():
            self._write(None)
            log.info("developer mode ended by itself at %s", _iso(until))
            until = None
        return {"on": until is not None, "until": _iso(until) if until is not None else None}

    def switch_on(self) -> dict[str, Any]:
        until = self.clock() + HOURS * 3600
        self._write(until)
        log.info("developer mode on until %s", _iso(until))
        return self.state()

    def switch_off(self, by: str) -> dict[str, Any]:
        was = self.state()["on"]
        self._write(None)
        log.info("developer mode off, asked by %s%s", by, "" if was else " (it was already off)")
        return self.state()


class Questions:
    """Questions waiting for the owner's spoken yes, each answered once by its token."""

    def __init__(self, clock: Callable[[], float] = time.monotonic, seconds: float = QUESTION_SECONDS) -> None:
        self.clock, self.seconds = clock, seconds
        self._open: dict[str, tuple[str, float, dict[str, Any]]] = {}

    def ask(self, kind: str, body: dict[str, Any]) -> str:
        now = self.clock()
        self._open = {t: q for t, q in self._open.items() if q[1] > now}
        token = secrets.token_hex(3)
        self._open[token] = (kind, now + self.seconds, body)
        return token

    def take(self, kind: str, token: str) -> dict[str, Any] | None:
        """The question's body, once, while it is fresh and of this kind; else None."""
        found = self._open.get(token)
        if found is None or found[0] != kind:
            return None
        del self._open[token]
        # RULE: a question token works once and only for two minutes
        if found[1] <= self.clock():
            return None
        return found[2]
