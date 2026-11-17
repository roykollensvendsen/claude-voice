"""Which versions of a repository's rights files the owner has accepted, by typing.

A Claude Code session's rights come from files: the project's ``.claude/settings.json`` and
``.claude/settings.local.json``. A session can write those files too, so a change on disk is not
a change the owner wanted. ``claude-voice permissions --accept <repo>`` shows each file's change
against the last version the owner accepted, the lines that widen a session's rights first, and
records the files only once the owner types the repository's name. Only what was shown is
recorded: a file that changed after it was shown refuses the accept.

The record, ``accepted.json`` in the bridge's state folder, holds each file's sha256 and where
the accept came from. A private copy of each accepted file is kept beside it, under
``accepted/<sha256>``, owner-only and outside every repository, so the next accept is shown
against the real contents and never against git. Rolling sessions onto accepted rights, and
writing shipped rules by themselves, build on this record (docs/permissions.md).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import safefile
from .config import STATE_DIR

RECORD = STATE_DIR / "accepted.json"
COPIES = STATE_DIR / "accepted"
SETTINGS = (Path(".claude/settings.json"), Path(".claude/settings.local.json"))
#: The lists of permission rules, and whether adding a rule there widens a session's rights.
LISTS = {"allow": True, "ask": False, "deny": False}
WIDENS, NARROWS = "WIDENS", "narrows"


class Changed(ValueError):
    """A file that is no longer what was shown; the message names it."""


@dataclass(frozen=True)
class Line:
    kind: str  # WIDENS or narrows
    text: str


@dataclass(frozen=True)
class Shown:
    """One rights file as it was shown: its bytes, their sha256, and its changed lines."""

    path: Path
    body: bytes
    sha256: str
    lines: tuple[Line, ...]


def sha256(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def read_record(record: Path | None = None) -> dict[str, Any]:
    try:
        found = json.loads((record or RECORD).expanduser().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    files = found.get("files") if isinstance(found, dict) else None
    return files if isinstance(files, dict) else {}


def _settings(body: bytes | None) -> dict[str, Any] | None:
    if body is None:
        return None
    try:
        parsed = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def changes(old: bytes | None, new: bytes) -> list[Line]:
    """The lines that differ between the accepted file and the one on disk, widening first.

    A new allow rule, or a removed ask or deny rule, widens. So does any other change: hooks,
    the environment, the default mode, extra folders, or any key this does not know. A new ask
    or deny rule, or a removed allow rule, only narrows. A file never accepted before, or one
    that is not a JSON table, is all widening."""
    before, after = _settings(old), _settings(new)
    if before is None or after is None:
        # RULE: a file never accepted before is shown as all widening
        return [Line(WIDENS, line) for line in new.decode("utf-8", errors="replace").splitlines()]
    lines = _rule_lines(before.get("permissions") or {}, after.get("permissions") or {})
    lines += _other_lines(before, after)
    # RULE: the lines that widen a sessions rights are shown first
    return sorted(lines, key=lambda line: line.kind != WIDENS)


def _rule_lines(rules_before: dict[str, Any], rules_after: dict[str, Any]) -> list[Line]:
    """Each permission rule added or removed, and whether that widens or narrows."""
    lines: list[Line] = []
    for name, adding_widens in LISTS.items():
        had, has = set(rules_before.get(name) or []), set(rules_after.get(name) or [])
        lines += [Line(WIDENS if adding_widens else NARROWS, f"+ {name} {r}") for r in sorted(has - had)]
        lines += [Line(NARROWS if adding_widens else WIDENS, f"- {name} {r}") for r in sorted(had - has)]
    return lines


def _other_lines(before: dict[str, Any], after: dict[str, Any]) -> list[Line]:
    """Every other key that changed, each counted as widening."""
    rules_before, rules_after = before.get("permissions") or {}, after.get("permissions") or {}
    others = {k for k in rules_before.keys() | rules_after.keys() if k not in LISTS}
    others |= {f"/{k}" for k in before.keys() | after.keys() if k != "permissions"}
    lines = []
    for key in sorted(others, key=lambda k: k.lstrip("/")):
        old_value, new_value = _value(before, key), _value(after, key)
        if old_value != new_value:
            lines.append(Line(WIDENS, f"~ {key.lstrip('/')}: {json.dumps(old_value)} -> {json.dumps(new_value)}"))
    return lines


def _value(settings: dict[str, Any], key: str) -> Any:
    if key.startswith("/"):
        return settings.get(key[1:])
    return (settings.get("permissions") or {}).get(key)


def _accepted_copy(path: Path, record: dict[str, Any], copies: Path) -> bytes | None:
    entry = record.get(str(path))
    if not isinstance(entry, dict) or not isinstance(entry.get("sha256"), str):
        return None
    try:
        return (copies.expanduser() / entry["sha256"]).read_bytes()
    except OSError:
        return None


def show(repo: Path, record: Path | None = None, copies: Path | None = None) -> list[Shown]:
    """Each rights file in `repo` that differs from its accepted version, with its changes."""
    copies = copies or COPIES
    accepted, shown = read_record(record), []
    for relative in SETTINGS:
        path = (repo / relative).resolve()
        try:
            body = path.read_bytes()
        except FileNotFoundError:
            continue
        digest = sha256(body)
        if (accepted.get(str(path)) or {}).get("sha256") == digest:
            continue
        shown.append(Shown(path, body, digest, tuple(changes(_accepted_copy(path, accepted, copies), body))))
    return shown


def text(shown: list[Shown]) -> str:
    """What the owner reads before typing: each file, then its lines, widening first."""
    out = []
    for item in shown:
        out.append(f"{item.path}:")
        out += [f"  {line.kind:<7} {line.text}" for line in item.lines] or ["  (no rule changed)"]
    return "\n".join(out) + "\n"


def accept(
    shown: list[Shown],
    source: str = "typed",
    record: Path | None = None,
    copies: Path | None = None,
    now: datetime | None = None,
) -> None:
    """Record exactly the files shown, as their shown bytes, or Changed when one moved since."""
    for item in shown:
        # RULE: a file changed after it was shown is not accepted
        if item.path.read_bytes() != item.body:
            raise Changed(f"{item.path} changed after it was shown; run the accept again and read it anew")
        try:
            item.body.decode("utf-8")
        except UnicodeDecodeError:
            # RULE: a file that is not utf8 text is refused and never copied wrongly
            raise Changed(f"{item.path} is not UTF-8 text, so it cannot be kept as accepted") from None
    folder = (copies or COPIES).expanduser()
    safefile.private_folder(folder)
    files = read_record(record)
    when = (now or datetime.now(UTC)).isoformat(timespec="seconds")
    for item in shown:
        # RULE: the accepted copy is the shown bytes exactly
        safefile.replace(folder / item.sha256, item.body)
        # RULE: an accept records only the files it showed
        files[str(item.path)] = {"sha256": item.sha256, "source": source, "at": when}
    target = (record or RECORD).expanduser()
    safefile.private_folder(target.parent, narrow=False)
    safefile.replace(target, json.dumps({"files": files}, indent=2, sort_keys=True) + "\n")
