"""Where a delivered turn came from, and who may put a turn into the support role.

The owner's yes, said to the support role, counts only when it came from him: typed in
support's own pane, or spoken through the voice. Every turn the bridge delivers therefore
starts with one line saying where it came from, and no session may deliver into support at
all. The manager's check reads that first line only. Design: support-role-v1, v1.1 b1.

This is a slip guard, not a wall: the fields live in files the owner's own Unix user can
write. It stays so until each role runs as its own user.
"""

from __future__ import annotations

from typing import Any

VOICE = ("claude_app", "voice_app")  # the caller kinds the voice reaches the bridge as
PAGE = "owner_page"  # the owner's phone page, signed in with the owner's own cookie or token
ROLE = "support"
FIRST = "origin: "


def stamp(caller: str, text: str) -> str:
    """`text` with a first line saying where it came from: the voice, or a session."""
    # RULE: a turn the voice delivers says so on its first line
    if caller in VOICE:
        return f"{FIRST}voice\n{text}"
    # RULE: a role the owners page starts says so on its first line
    if caller == PAGE:
        return f"{FIRST}owner page\n{text}"
    # RULE: a turn anything else delivers is stamped as a session
    return f"{FIRST}session {caller}\n{text}"


def refused(caller: str, target: dict[str, Any], support: dict[str, Any] | None) -> str | None:
    """Why `caller` may not deliver a turn into `target`, or None when it may.

    `support` is the bridge's record of the running support role: its name and session id.
    """
    if caller in VOICE or not support:
        return None
    named = target.get("name") == support.get("name")
    same = bool(support.get("id")) and str(target.get("id")) == str(support.get("id"))
    # RULE: no session may deliver a turn into the support role
    if named or same:
        return (
            "Refused: no session may send a turn into the support role, because its user turns "
            "may carry the owner's yes. Send it to the manager, claude-voice-work, instead."
        )
    return None


def refused_role(caller: str, role: str) -> str | None:
    """Why `caller` may not start `role`, or None when it may."""
    # RULE: no session may start the support role
    if role == ROLE and caller not in (*VOICE, PAGE):
        return "Refused: only the owner starts the support role, typed or by voice."
    return None
