"""What the hub signs, and how the helper checks it.

The hub holds an Ed25519 private key. The helper holds only the public one, so a process on
this machine can read it and still not sign. Every field is a plain word, so the bytes that
are signed can mean one thing only.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import re

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

ACTIONS = ("poweroff", "reboot")
WORD = re.compile(r"[a-z0-9][a-z0-9-]{0,62}")


class BadField(ValueError):
    """A field that is not a plain word, so it could change what the signature covers."""


def _word(name: str, value: str, empty_ok: bool = False) -> str:
    if (empty_ok and value == "") or WORD.fullmatch(value):
        return value
    raise BadField(f"{name} is not a plain word")


def request_payload(machine: str, action: str, at: int) -> bytes:
    """The bytes the hub signs to open a question: this machine, this action, this second."""
    if action not in ACTIONS:
        raise BadField("action is not poweroff or reboot")
    return "|".join(("request", _word("machine", machine), action, str(int(at)))).encode()


def confirm_payload(machine: str, action: str, nonce: str, expires: int, confirm_name: str) -> bytes:
    """The bytes the hub signs to answer yes: machine, action, the helper's nonce, an expiry
    and the machine's name when the question asked for it."""
    if action not in ACTIONS:
        raise BadField("action is not poweroff or reboot")
    # RULE: confirm_name is part of the signed payload
    fields = ("confirm", _word("machine", machine), action, _word("nonce", nonce), str(int(expires)), confirm_name)
    _word("confirm_name", confirm_name, empty_ok=True)
    return "|".join(fields).encode()


class Verifier:
    """Checks signatures against the hub's public key."""

    def __init__(self, public_key: bytes) -> None:
        self.key = Ed25519PublicKey.from_public_bytes(public_key)

    def ok(self, payload: bytes, signature: str) -> bool:
        try:
            self.key.verify(base64.b64decode(signature, validate=True), payload)
        except (InvalidSignature, binascii.Error, ValueError):
            return False
        return True


def parse_public_key(text: str) -> bytes:
    """The 32 bytes of a public key written as one line of base64."""
    try:
        raw = base64.b64decode(text.strip(), validate=True)
    except (binascii.Error, ValueError):
        raw = b""
    if len(raw) != 32:
        raise BadField("the hub key is not 32 bytes of base64")
    return raw


def fingerprint(public_key: bytes) -> str:
    """The key's sha256 in eight groups of eight, to compare by eye with the hub's."""
    digest = hashlib.sha256(public_key).hexdigest()
    return " ".join(digest[i : i + 8] for i in range(0, 64, 8))
