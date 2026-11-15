"""The helper's decisions: a signed question, a signed yes, one minute, and a second look.

Nothing here talks to a socket or a clock of its own: the server hands it messages and the
time, and a test hands it a fake login manager. It never calls the login manager's scheduled
shutdown, so every scheduled shutdown it finds is somebody else's, and it never cancels one.
"""

from __future__ import annotations

import logging
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from . import signing
from .logind import Logind, LogindError
from .look import Look, look

log = logging.getLogger("claude_voice_power")

REQUEST_SECONDS = 10  # a guess: how old a signed request may be
SKEW_SECONDS = 2  # a guess: how far ahead the hub's clock may run
NONCE_SECONDS = 60
MINUTE_SECONDS = 60
NIGHT = (23, 7)  # a guess: from 23:00 to 07:00 on this machine's own clock

Answer = dict[str, Any]


class Notifier(Protocol):
    def tell(self, text: str) -> None: ...


@dataclass
class Question:
    nonce: str
    action: str
    born: float
    needs_name: bool


def _refuse(say: str, code: str) -> Answer:
    return {"ok": False, "code": code, "say": say}


def at_night(hour: int, window: tuple[int, int] = NIGHT) -> bool:
    start, end = window
    return hour >= start or hour < end if start > end else start <= hour < end


class Helper:
    def __init__(
        self,
        logind: Logind,
        verifier: signing.Verifier,
        machine: str,
        notifier: Notifier,
        now: Callable[[], float] = time.time,
        monotonic: Callable[[], float] = time.monotonic,
        local_time: Callable[[], tuple[int, int]] = lambda: (time.localtime().tm_hour, time.localtime().tm_min),
        night: tuple[int, int] = NIGHT,
    ) -> None:
        self.logind, self.verifier, self.machine, self.notifier = logind, verifier, machine, notifier
        self.now, self.monotonic, self.local_time, self.night = now, monotonic, local_time, night
        self.open: Question | None = None
        self.replaced: set[str] = set()
        self.due: float | None = None
        self.action = ""

    # -- the three things the hub's agent may relay --------------------------------------

    def request(self, machine: str, action: str, at: int, signature: str) -> Answer:
        """A signed question: the look, and a nonce when the answer may be yes."""
        try:
            payload = signing.request_payload(machine, action, at)
        except signing.BadField:
            return _refuse("That request is not in a form I accept.", "malformed")
        age = self.now() - at
        # RULE: a request needs the hub signature and a time under 10 seconds old
        if not (self.verifier.ok(payload, signature) and -SKEW_SECONDS <= age <= REQUEST_SECONDS):
            return _refuse("That request is not signed by the hub, or it is too old.", "unsigned")
        if machine != self.machine:
            return _refuse("That request is for another machine.", "other-machine")
        if self.due is not None:
            return _refuse(f"{self.machine} is already counting down; cancel that first.", "counting")
        if self.open is not None:
            # RULE: a new request replaces the open question
            self.replaced.add(self.open.nonce)
            self.open = None
        self.replaced = self.replaced if len(self.replaced) < 16 else set()
        seen = self._look()
        if seen.refused:
            return _refuse(seen.refused, "refused")
        # RULE: at night the confirm needs the machine name
        needs_name = seen.needs_name or self._night()
        question = Question(secrets.token_hex(16), action, self.now(), needs_name)
        self.open = question
        return {
            "ok": True,
            "nonce": question.nonce,
            "needs_name": needs_name,
            "say": self._question_text(action, seen, needs_name),
        }

    def confirm(self, machine: str, action: str, nonce: str, expires: int, confirm_name: str, signature: str) -> Answer:
        """A signed yes: the second look, and then the helper's own minute."""
        try:
            payload = signing.confirm_payload(machine, action, nonce, expires, confirm_name)
        except signing.BadField:
            return _refuse("That answer is not in a form I accept.", "malformed")
        now = self.now()
        fresh = now < expires <= now + NONCE_SECONDS + SKEW_SECONDS
        # RULE: a power action needs the hub signature over this machine, this action, this nonce and an unexpired time
        if not (self.verifier.ok(payload, signature) and fresh and machine == self.machine):
            return _refuse("That answer is not signed by the hub for this question.", "unsigned")
        question = self.open
        # RULE: a nonce works once, for its own action, within 60 seconds
        valid = _belongs(question, nonce, action, now)
        if question is None or not valid:
            if nonce in self.replaced:
                return _refuse("That question was replaced by a new one.", "replaced")
            return _refuse("There is no open question for that answer; ask again.", "no-question")
        if question.needs_name and confirm_name != self.machine:  # the question stays open for a better yes
            return _refuse(f"Say 'yes, {_verb(action)} {self.machine}' to go ahead.", "needs-name")
        self.open = None
        seen = self._look()
        if seen.refused:
            return _refuse(seen.refused, "refused")
        self.due, self.action = now + MINUTE_SECONDS, action
        self.notifier.tell(
            f"{self.machine} will {_verb(action)} in one minute, by voice. "
            "To stop it, run: /usr/local/lib/claude-voice-power/bin/claude-voice-power cancel"
        )
        return {"ok": True, "say": f"{self.machine} will {_verb(action)} in one minute."}

    def cancel(self) -> Answer:
        """Stops the helper's own minute. Needs no signature: stopping is always safe."""
        was, self.due, self.open = self.due, None, None
        if was is not None:
            self.notifier.tell(f"The {_verb(self.action)} of {self.machine} was cancelled.")
        return {"ok": True, "say": "Cancelled." if was is not None else "Nothing was planned."}

    # -- the minute ----------------------------------------------------------------------

    def tick(self) -> Answer | None:
        """Called often. When the minute is up: look once more, then ask the login manager."""
        if self.due is None or self.now() < self.due:
            return None
        action, self.due = self.action, None
        # RULE: the helper looks again just before it acts
        again = self._look(check_inhibitors=False)
        if again.refused:
            self.notifier.tell(f"{self.machine} stays on: {again.refused}")
            return _refuse(again.refused, "refused")
        try:
            self.logind.power(action)
        except LogindError as stopped:
            self.notifier.tell(f"{self.machine} stays on: {stopped}")
            return _refuse(f"{self.machine} stays on: {stopped}", "stopped")
        log.info("power: %s started", action)
        return {"ok": True, "say": f"{self.machine} is going down."}

    # -- helpers -------------------------------------------------------------------------

    def _look(self, check_inhibitors: bool = True) -> Look:
        return look(self.logind, self.machine, int(self.monotonic() * 1_000_000), check_inhibitors)

    def _night(self) -> bool:
        return at_night(self.local_time()[0], self.night)

    def _question_text(self, action: str, seen: Look, needs_name: bool) -> str:
        parts = [f"{_verb(action).capitalize()} {self.machine} in one minute?", *(note + "." for note in seen.notes)]
        if seen.blocked:
            parts.append("An open program will stop it.")
        if self._night():
            hour, minute = self.local_time()
            parts.append(f"It's {hour:02d}:{minute:02d} on {self.machine}.")
        if needs_name:
            parts.append(f"Say 'yes, {_verb(action)} {self.machine}' to go ahead.")
        return " ".join(parts)

    def handle(self, message: dict[str, Any]) -> Answer:
        """One message from the socket, answered; anything unexpected is refused, not raised."""
        try:
            op = message.get("op")
            if op == "request":
                return self.request(
                    str(message["machine"]), str(message["action"]), int(message["at"]), str(message["signature"])
                )
            if op == "confirm":
                return self.confirm(
                    str(message["machine"]),
                    str(message["action"]),
                    str(message["nonce"]),
                    int(message["expires"]),
                    str(message.get("confirm_name", "")),
                    str(message["signature"]),
                )
            if op == "cancel":
                return self.cancel()
        except (KeyError, TypeError, ValueError):
            pass
        return _refuse("That message is not one I understand.", "malformed")


def _belongs(question: Question | None, nonce: str, action: str, now: float) -> bool:
    """Whether the open question is the one this answer is for, and still within its minute."""
    if question is None:
        return False
    return question.nonce == nonce and question.action == action and now <= question.born + NONCE_SECONDS


def _verb(action: str) -> str:
    return "restart" if action == "reboot" else "shut down"
