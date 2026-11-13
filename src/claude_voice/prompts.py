"""The questions and approvals that sessions' hooks have handed to the bridge, while they last.

The phone hook opens a prompt and then waits on it; the phone's answer reaches the
session through that wait and no other way. So a prompt nobody waits on can no
longer be answered, and it is closed rather than left on the phone for ever. The
bridge sweeps the book every few seconds with Claude Code's registry of running
sessions, which says why it closed: the session ended, it moved on, or the hook
simply went away.
"""

from __future__ import annotations

import contextlib
import logging
import time
from collections.abc import Callable, Iterator
from datetime import datetime
from typing import Any

GRACE = 30.0  # seconds a prompt may go with no hook waiting on it before it closes
RECENT = 5.0  # seconds after a hook's wait ends that it still counts: its next wait is on its way

log = logging.getLogger("claude_voice.prompts")


class Book:
    def __init__(
        self,
        publish: Callable[[str, dict[str, Any]], None] = lambda kind, data: None,
        clock: Callable[[], float] = time.monotonic,
        waited_on: Callable[[dict[str, Any]], None] = lambda prompt: None,
    ) -> None:
        self.items: dict[str, dict[str, Any]] = {}  # open prompts by id, answered ones until collected
        self.publish, self.clock = publish, clock
        self.waited_on = waited_on  # told once per prompt, when a hook first waits on it

    def add(self, prompt: dict[str, Any]) -> None:
        # "ended" is when a hook's wait last ended: None until a hook has waited at all.
        prompt["hook"] = {"waiting": 0, "last": self.clock(), "ended": None, "told": False}
        self.items[prompt["id"]] = prompt

    @contextlib.contextmanager
    def waiting(self, prompt: dict[str, Any]) -> Iterator[None]:
        """Held while the hook's wait call is under way, so the prompt is kept."""
        hook = prompt["hook"]
        hook["waiting"] += 1
        try:
            # RULE: a question is news once a hook waits on it
            if not hook["told"]:
                hook["told"] = True
                self._tell(prompt)
            yield
        finally:
            hook["waiting"] -= 1
            hook["last"] = hook["ended"] = self.clock()

    def _tell(self, prompt: dict[str, Any]) -> None:
        """Pass on that a hook now waits on the prompt; a failure there never stops the wait."""
        try:
            self.waited_on(prompt)
        # RULE: a wait goes on and is counted off when its news fails
        except Exception:
            log.exception("could not tell the news of prompt %s", prompt.get("id"))

    def hook_waits(self, prompt: dict[str, Any]) -> bool:
        """Whether a hook waits on the prompt now, or did a moment ago and is about to again."""
        hook = prompt["hook"]
        ended = hook["ended"]
        # RULE: a prompt counts as waited on only while a hook waits or did a moment ago
        return bool(hook["waiting"]) or (ended is not None and self.clock() - ended < RECENT)

    def close(self, prompt_id: str, how: str) -> dict[str, Any] | None:
        """Take a prompt out; an unanswered one is announced as closed, with how."""
        prompt = self.items.pop(prompt_id, None)
        if prompt is not None and prompt.get("answer") is None:
            self.publish("prompt_closed", {"id": prompt_id, "how": how})
        return prompt

    def sweep(self, live: list[dict[str, Any]]) -> list[tuple[str, str]]:
        """Close every prompt no hook waits on any more; the ids closed, each with why."""
        rows = {str(row.get("sessionId")): row for row in live}
        closed = []
        for prompt_id, prompt in list(self.items.items()):
            how = self._why_closed(prompt, rows.get(prompt["session"]))
            # RULE: a prompt with no hook waiting on it closes
            if how:
                self.close(prompt_id, how)
                closed.append((prompt_id, how))
        return closed

    def _why_closed(self, prompt: dict[str, Any], row: dict[str, Any] | None) -> str | None:
        hook = prompt["hook"]
        # RULE: a prompt the hook still waits on is never closed
        if hook["waiting"] or self.clock() - hook["last"] < GRACE:
            return None
        if row is None:
            return "ended"
        if row.get("status") != "waiting" and _ms(row.get("statusUpdatedAt")) > _asked_ms(prompt):
            return "moved_on"
        return "hook_gone"


def _ms(value: Any) -> float:
    return float(value) if isinstance(value, int | float) else 0.0


def _asked_ms(prompt: dict[str, Any]) -> float:
    try:
        return datetime.fromisoformat(str(prompt.get("asked_at"))).timestamp() * 1000
    except ValueError:
        return 0.0
