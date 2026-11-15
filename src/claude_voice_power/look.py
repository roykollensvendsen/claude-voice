"""The read-only look: who is at this machine, and what stands in the way.

It only reads. It answers one of three things: refused, with the reason; a question to put
to the owner, perhaps one that needs the machine's name in the yes; and notes to read aloud.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .logind import Logind

IDLE_LIMIT_USEC = 10 * 60 * 1_000_000  # a guess: ten minutes without input means nobody is at it


@dataclass
class Look:
    refused: str | None = None
    needs_name: bool = False
    notes: list[str] = field(default_factory=list)
    blocked: bool = False  # an inhibitor will stop the action


def look(logind: Logind, machine: str, now_usec: int, check_inhibitors: bool = True) -> Look:
    """What a person at `machine` would want known before it goes down."""
    found = Look()
    remote, text = [], []
    for session in logind.sessions():
        if session.klass != "user":
            continue
        if session.remote:
            remote.append(session.name)
        elif not session.graphical:
            text.append(f"{session.name} on {session.tty or 'a text login'}")
        elif session.locked or not session.active:
            # RULE: a locked screen with a login open needs the machine name in the yes
            found.needs_name = True
            found.notes.append(f"{machine}'s screen is locked, with {session.name} logged in")
        elif not (session.idle and session.idle_since and now_usec - session.idle_since >= IDLE_LIMIT_USEC):
            # RULE: a machine in use by someone at it is never shut down or restarted
            found.refused = f"Someone is using {machine} right now, so I'll leave it."
    if remote:
        found.notes.append(f"{len(remote)} remote login: {', '.join(remote)}")
    if text:
        found.notes.append(f"{len(text)} text login: {', '.join(text)}")
    planned = logind.scheduled()
    if planned is not None:
        # RULE: a shutdown not made by the helper is never cancelled or replaced
        found.refused = f"{machine} already has a {planned.kind} planned; I'll leave it."
    if check_inhibitors:
        for inhibitor in logind.inhibitors():
            found.notes.append(f"{inhibitor.who} holds it up: {inhibitor.why}")
            found.blocked = found.blocked or inhibitor.blocks
    return found
