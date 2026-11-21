"""Where a delivered turn came from, and who may put a turn into the support role.

The owner's yes, said to the support role, counts only when it came from him: typed in
support's own pane, or spoken through the voice. Every turn the bridge delivers therefore
starts with one line saying where it came from. The manager's check reads that first line
only. Design: support-role-v1, v1.1 b1.

Support is reached only through its own two tools, tell_support and start_support. The general
tools (send_task, message_active_session, ask_active_session, start_role) refuse it for every
caller, the voice included. Support's own tools serve the Claude app's caller kind and the
owner's page, and nothing else.

How a call arrives does not tell every session from the voice. So every session is also
denied support's two tools by name, under both names the bridge is installed as (the shipped
deny in permissions.json, which `claude-voice permissions` writes). The bridge itself never
looks at a tool's prefix. A session that added its own server entry pointing at
the bridge would need the bridge's bearer token, which no session may read.

These are slip guards, not walls:
- the deny is a list of names in a settings file the owner's own Unix user can write;
- `voice_app` is any caller on this machine that sends a trace id, which a local program holding
  the token can claim, so support's own tools do not accept it;
- the stamps live in transcript files the same user can write.
It stays so until each role runs as its own user.
"""

from __future__ import annotations

from typing import Any

VOICE = ("claude_app", "voice_app")  # the caller kinds the voice reaches the bridge as
DOORS = ("claude_app", "owner_page")  # the caller kinds support's own tools serve
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


def refused(target: dict[str, Any], support: dict[str, Any] | None) -> str | None:
    """Why a general tool may not deliver a turn into `target`, or None when it may.

    `support` is the bridge's record of the running support role: its name and session id.
    """
    if not support:
        return None
    named = target.get("name") == support.get("name")
    same = bool(support.get("id")) and str(target.get("id")) == str(support.get("id"))
    # RULE: the general tools never deliver a turn into support
    if named or same:
        return (
            "Refused: the support role takes turns only through tell_support, because its user turns "
            "may carry the owner's yes. A session sends to the manager, claude-voice-work, instead."
        )
    return None


def refused_role(caller: str, role: str) -> str | None:
    """Why `caller` may not start `role` through start_role, or None when it may."""
    # RULE: only the owners page starts support through start_role
    if role == ROLE and caller != PAGE:
        return "Refused: the support role is started with start_support, by the owner's voice."
    return None


def refused_door(caller: str) -> str | None:
    """Why `caller` may not use support's own tools, or None when it may."""
    # RULE: only the voice uses supports own tools
    if caller not in DOORS:
        return "Refused: only the owner's voice speaks to the support role through this tool."
    return None
