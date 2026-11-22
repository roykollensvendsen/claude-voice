"""The approver's own token, which opens only the two approver tools (approver-v1, v1.7).

The bridge mints it when it launches the approver, and pins it to the pane's process: the pid
tmux printed and the kernel's start ticks for it. The token is handed over once, to that
process only, over the report socket. After that the bridge keeps only its hash, in memory.
It opens nothing once that process is gone or a new launch is made, and a restarted bridge
has no hash at all.

Known limit: the approver holds the token in its environment, which the same Unix user can
read, and the hook it runs inherits it. What a reader gains is the two approver tools while
the approver lives; the report proof still decides who may answer a box.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

from . import lineage


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class Keeper:
    """The one approver token there is at a time, and the process it belongs to."""

    def __init__(self) -> None:
        self.revoke()

    def revoke(self) -> None:
        self._waiting: str | None = None  # the token itself, only until it is handed over
        self._hash: str | None = None
        self._pin: tuple[int, str | None] | None = None

    def mint(self, pid: int) -> None:
        """A new token for the approver in pane process `pid`; any older one stops opening."""
        token = secrets.token_urlsafe(32)
        self._waiting, self._hash = token, _hash(token)
        self._pin = (pid, lineage.start_ticks(pid))

    def _alive(self) -> bool:
        pid, started = self._pin or (0, None)
        # RULE: the approvers token dies with its pane
        return started is not None and lineage.start_ticks(pid) == started

    def hand(self, peer: int | None) -> str | None:
        """The token, once, to the pinned process only; None to anyone else or any later ask."""
        token, pid = self._waiting, (self._pin or (0, None))[0]
        # RULE: the approvers token goes only to its own pane
        if token is None or peer != pid or not self._alive():
            return None
        # RULE: the approvers token is handed over once
        self._waiting = None
        return token

    def valid(self, token: str) -> bool:
        """Whether `token` is the approver's, handed over, and its process still lives."""
        if not token or self._hash is None or self._waiting is not None:
            return False
        return hmac.compare_digest(_hash(token), self._hash) and self._alive()
