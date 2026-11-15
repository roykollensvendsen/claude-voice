"""What the power tests share: a login manager that powers nothing off, and the hub's key.

Nothing here reaches a real login manager, a real socket of the system or a real machine.
"""

from __future__ import annotations

import base64
from dataclasses import replace

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from claude_voice_power import signing
from claude_voice_power.logind import Inhibitor, LogindError, Scheduled, Session

MACHINE = "laptop-b"


def session(**changes) -> Session:
    base = Session("1", "alex", "wayland", "user", False, "", True, False, False, 0)
    return replace(base, **changes)


class FakeLogind:
    """Holds sessions, inhibitors and a scheduled shutdown, and records what it was asked."""

    def __init__(self, sessions=(), inhibitors=(), scheduled=None):
        self._sessions, self._inhibitors, self._scheduled = list(sessions), list(inhibitors), scheduled
        self.powered: list[str] = []
        self.calls: list[str] = []

    def sessions(self):
        return list(self._sessions)

    def inhibitors(self):
        return list(self._inhibitors)

    def scheduled(self):
        return self._scheduled

    def power(self, action):
        self.calls.append(action)
        blocking = [i for i in self._inhibitors if i.blocks]
        if blocking:
            raise LogindError(f"{blocking[0].who} is blocking it")
        self.powered.append(action)


def inhibitor(mode="block", who="Editor", why="a file is unsaved", what="shutdown"):
    return Inhibitor(what, who, why, mode)


def planned(kind="poweroff", at=1_800_000_000_000_000):
    return Scheduled(kind, at)


class Hub:
    """A key pair standing in for the hub's: the private half signs, the helper holds the other."""

    def __init__(self):
        self.private = Ed25519PrivateKey.generate()
        self.public = self.private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)

    def sign(self, payload: bytes) -> str:
        return base64.b64encode(self.private.sign(payload)).decode()

    def request(self, action="poweroff", at=1000, machine=MACHINE) -> dict:
        signature = self.sign(signing.request_payload(machine, action, at))
        return {"machine": machine, "action": action, "at": at, "signature": signature}

    def confirm(self, nonce, action="poweroff", expires=1050, confirm_name="", machine=MACHINE) -> dict:
        payload = signing.confirm_payload(machine, action, nonce, expires, confirm_name)
        return {
            "machine": machine,
            "action": action,
            "nonce": nonce,
            "expires": expires,
            "confirm_name": confirm_name,
            "signature": self.sign(payload),
        }


class Notes:
    def __init__(self):
        self.told: list[str] = []

    def tell(self, text):
        self.told.append(text)


class Clock:
    """The wall clock, the monotonic clock and the local hour, moved together."""

    def __init__(self, now=1000.0):
        self.now, self.mono, self.hour = now, 5000.0, (14, 5)

    def __call__(self):
        return self.now

    def monotonic(self):
        return self.mono

    def local(self):
        return self.hour

    def pass_seconds(self, seconds):
        self.now += seconds
        self.mono += seconds
