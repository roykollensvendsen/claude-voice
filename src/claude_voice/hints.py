"""Hints that sessions in no programme seem to belong together.

The phone page shows the sessions in no programme under "Other". When some of
them look like one piece of work, the page shows a hint that they might want a
programme of their own. The bridge only suggests; nobody's chart changes.

Three reasons, strongest first, and each session in one hint at most: the
sessions sent each other messages in the last week, they work in the same
folder, or their names start the same way.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterable
from pathlib import Path, PurePath
from typing import Any

WINDOW_SECONDS = 7 * 24 * 3600  # how far back messages count
MIN_PREFIX = 4  # the shortest name prefix, its dash included

Rows = dict[str, dict[str, Any]]
Counts = dict[tuple[str, str], int]


def hint_id(sessions: Iterable[str]) -> str:
    """A short id that stays the same for the same sessions, in whatever order."""
    # RULE: the same sessions always get the same hint id
    joined = ",".join(sorted(sessions))
    return "h-" + hashlib.sha1(joined.encode()).hexdigest()[:10]


def _hint(sessions: list[str], reason: str, detail: str | None, messages: int | None) -> dict[str, Any]:
    ids = sorted(sessions)
    return {"id": hint_id(ids), "sessions": ids, "reason": reason, "detail": detail, "messages": messages}


def _enough(sessions: list[str]) -> bool:
    # RULE: a hint holds two sessions or more
    return len(sessions) >= 2


def folder_key(cwd: str | None, root: Path) -> str | None:
    """The folder a session works in, as far as belonging together goes: the project
    folder under the root for anything inside it, a worktree's repository for the worktree."""
    if not cwd:
        return None
    path = PurePath(cwd)
    parts = path.parts
    for i in range(len(parts) - 1):
        if parts[i] == ".claude" and parts[i + 1] == "worktrees":
            # RULE: a worktree is grouped with its repository
            path = PurePath(*parts[:i])
            break
    top = PurePath(root)
    # RULE: the project root is no shared folder
    if path == top:
        return None
    if top in path.parents:
        return str(top / path.relative_to(top).parts[0])
    return str(path)


def _by_messages(left: Rows, folders: dict[str, str], counts: Counts) -> list[dict[str, Any]]:
    """Sessions joined by messages, directly or through each other."""
    group = {sid: sid for sid in left}

    def top(sid: str) -> str:
        while group[sid] != sid:
            sid = group[sid]
        return sid

    links = [(a, b, n) for (a, b), n in counts.items() if a in left and b in left and n > 0]
    for a, b, _ in links:
        group[top(a)] = top(b)
    members: dict[str, list[str]] = {}
    for sid in left:
        members.setdefault(top(sid), []).append(sid)
    out = []
    for sessions in members.values():
        if _enough(sessions):
            total = sum(n for a, _, n in links if top(a) == top(sessions[0]))
            out.append(_hint(sessions, "messages", None, total))
    return out


def _by_folder(left: Rows, folders: dict[str, str], counts: Counts) -> list[dict[str, Any]]:
    members: dict[str, list[str]] = {}
    for sid in left:
        if folders.get(sid):
            members.setdefault(folders[sid], []).append(sid)
    return [_hint(s, "folder", PurePath(f).name, None) for f, s in sorted(members.items()) if _enough(s)]


def _prefixes(name: Any) -> list[str]:
    """Each start of the name that ends in a dash and is long enough, longest first."""
    text = str(name or "")
    # RULE: a name prefix ends in a dash and is at least four characters
    return [text[: i + 1] for i in range(len(text) - 1, -1, -1) if text[i] == "-" and i + 1 >= MIN_PREFIX]


def _by_prefix(left: Rows, folders: dict[str, str], counts: Counts) -> list[dict[str, Any]]:
    starts = {sid: _prefixes(row.get("name")) for sid, row in left.items()}
    taken: set[str] = set()
    out = []
    for prefix in sorted({p for found in starts.values() for p in found}, key=lambda p: (-len(p), p)):
        sessions = [sid for sid in left if sid not in taken and prefix in starts[sid]]
        if _enough(sessions):
            out.append(_hint(sessions, "prefix", prefix, None))
            taken.update(sessions)
    return out


Reason = Callable[[Rows, dict[str, str], Counts], list[dict[str, Any]]]


def hints(rows: list[dict[str, Any]], folders: dict[str, str], counts: Counts) -> list[dict[str, Any]]:
    """The hints for the page. `rows` are the sessions placed in the org chart, `folders`
    each one's folder by id (see `folder_key`), `counts` the messages between two ids."""
    # RULE: only sessions in no programme are hinted
    left = {str(r["id"]): r for r in rows if r.get("programme") is None}
    found: list[dict[str, Any]] = []
    # RULE: the strongest reason places a session first
    reasons: tuple[Reason, ...] = (_by_messages, _by_folder, _by_prefix)
    for reason in reasons:
        for hint in reason(left, folders, counts):
            found.append(hint)
            for sid in hint["sessions"]:
                # RULE: a session is in one hint at most
                left.pop(sid)
    return found
