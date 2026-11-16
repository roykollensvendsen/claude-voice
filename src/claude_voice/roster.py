"""The sessions file: one row per role, kept so the sessions can be brought back (ADR-CV-019).

The bridge writes it from each look at the running sessions. A row says which organisation
and role a session is, where it works, which conversation it runs and which tmux pane holds
it. A row stays when its session ends, because that is when recovery needs it.
"""

from __future__ import annotations

import tomllib
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any

import tomli_w

from . import safefile
from .config import STATE_DIR

FILE = STATE_DIR / "sessions.toml"
SOLO = "solo"  # the organisation of a session in no programme
HEADER = "# Written by the claude-voice bridge: one row per role, for bringing sessions back.\n\n"


class Unreadable(ValueError):
    """The sessions file is there but cannot be read; the message says what to do."""


@dataclass(frozen=True)
class Row:
    organisation: str
    role: str
    folder: str
    conversation: str
    tmux: str
    last_seen: str


FIELDS = tuple(f.name for f in fields(Row))


def _row(d: dict[str, Any], organisation: str | None, now: str) -> Row:
    return Row(
        organisation or SOLO,
        str(d["name"]),
        str(d.get("cwd") or ""),
        str(d["sessionId"]),
        str(d.get("tmux") or ""),
        now,
    )


def _is_role(d: dict[str, Any]) -> bool:
    """A session in a terminal, with a name and a conversation; the background ones are helpers."""
    return d.get("kind") == "interactive" and bool(d.get("name")) and bool(d.get("sessionId"))


def seen(
    rows: Iterable[Row],
    live: Iterable[dict[str, Any]],
    organisation: Callable[[dict[str, Any]], str | None],
    now: str,
) -> list[Row]:
    """The rows after a look at the running sessions `live` (Claude Code's records of them),
    each placed in its organisation by `organisation`; None is no programme."""
    # RULE: a role that has gone stays in the sessions file
    kept = {(r.organisation, r.role): r for r in rows}
    for d in filter(_is_role, live):
        row = _row(d, organisation(d), now)
        # RULE: a conversation is listed under one role only
        kept = {key: r for key, r in kept.items() if r.conversation != row.conversation}
        kept[(row.organisation, row.role)] = row
    return [kept[key] for key in sorted(kept)]


def dump(rows: Iterable[Row]) -> str:
    return HEADER + tomli_w.dumps({"session": [asdict(r) for r in rows]})


def read(path: Path) -> list[Row]:
    """The rows in the file at `path`; none when it is missing. A row missing a field is skipped."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    try:
        found = tomllib.loads(text).get("session", [])
    except tomllib.TOMLDecodeError as exc:
        raise Unreadable(
            f"{path} cannot be read ({exc}). The bridge writes it again at its next look: remove it, "
            "then wait a few seconds"
        ) from exc
    return [Row(**{k: str(r[k]) for k in FIELDS}) for r in found if isinstance(r, dict) and set(FIELDS) <= set(r)]


def write(path: Path, rows: Iterable[Row]) -> bool:
    """Write `rows` as the whole file, owner-only, unless it already says that; whether it wrote."""
    text = dump(rows)
    try:
        if path.read_text(encoding="utf-8") == text:
            return False
    except FileNotFoundError:
        pass
    safefile.private_folder(path.parent, narrow=False)
    safefile.replace(path, text)
    return True
