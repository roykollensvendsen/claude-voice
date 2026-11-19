"""The workflow event, version 1: one line of the friction log, and how a line is read back.

docs/workflow-event/v1.json publishes the same format for other programs. An event
that breaks the format cannot be made, so neither the gate nor the reader can let
one through. A reader accepts this version and the one before it; any other
version is skipped and counted, never guessed at. Version 1 has no version before
it, so today only 1 is read.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

VERSION = 1
KINDS = ("conflict", "permission", "flaky", "slow", "waiting", "stall", "other")
BLOCKERS = ("roy", "owner", "review", "ci", "permission", "session", "outside", "none")
#: Old names a stored line may still hold, and what they are read as. Kept for good: a log
#: keeps its lines for as long as they exist.
OLD_BLOCKERS = {"roy": "owner"}
STATES = ("open", "closed", "point")
#: The longest `line` kept, the same limit the bridge's notes have.
LINE_CHARS = 120
#: Every field of a version 1 line, in the order it is written; `closed_late` only on a late close.
FIELDS = (
    "v",
    "at",
    "source",
    "id",
    "session",
    "repo",
    "kind",
    "blocker",
    "state",
    "started",
    "duration_s",
    "what",
    "line",
)


class EventError(ValueError):
    """An event that breaks the format. The message names the field, never its value."""


@dataclass(frozen=True)
class Event:
    """One friction point, or the opening or closing of one. Times carry their offset."""

    at: datetime
    source: str
    id: str
    session: str
    repo: str
    kind: str
    blocker: str
    state: str
    started: datetime
    what: str | None = None
    line: str | None = None
    closed_late: bool = False

    def __post_init__(self) -> None:
        wrong = _fault(self)
        if wrong:
            raise EventError(wrong)

    @property
    def duration_s(self) -> int | None:
        """How long the friction lasted, in whole seconds; only a close has one."""
        seconds = int((self.at - self.started).total_seconds())
        # RULE: a duration is set only on a closed event
        return seconds if self.state == "closed" else None

    def to_dict(self) -> dict[str, Any]:
        """The event as its line holds it, `v` first."""
        body: dict[str, Any] = {"v": VERSION}
        for name in FIELDS[1:]:
            value = getattr(self, name)
            body[name] = value.isoformat(timespec="seconds") if isinstance(value, datetime) else value
        if self.closed_late:
            body["closed_late"] = True
        return body

    def to_line(self) -> str:
        """The event as one line of the log, without its newline."""
        return json.dumps(self.to_dict(), ensure_ascii=False)


def _fault(e: Event) -> str | None:
    """What is wrong with an event, by field, or None."""
    # RULE: an event kind outside the fixed list is refused
    if e.kind not in KINDS:
        return "the kind is not one of the fixed list"
    # RULE: a blocker outside the fixed list is refused
    if e.blocker not in BLOCKERS:
        return "the blocker is not one of the fixed list"
    # RULE: a state outside the fixed list is refused
    if e.state not in STATES:
        return "the state is not one of the fixed list"
    return _time_fault(e) or _text_fault(e)


def _time_fault(e: Event) -> str | None:
    # RULE: an event time carries its offset
    if e.at.utcoffset() is None or e.started.utcoffset() is None:
        return "a time has no offset"
    # RULE: an event cannot end before it starts
    if e.at < e.started:
        return "it ends before it starts"
    # RULE: closed late is only on a closed event
    if e.closed_late and e.state != "closed":
        return "only a closed event can be closed late"
    return None


def _text_fault(e: Event) -> str | None:
    if not all(isinstance(v, str) and v for v in (e.source, e.id, e.session, e.repo)):
        return "source, id, session and repo must be text"
    if not e.id.startswith(e.source + ":"):
        return "the id does not start with its source"
    if not all(v is None or isinstance(v, str) for v in (e.what, e.line)):
        return "what and line must be text or null"
    # RULE: an event line is never longer than the limit
    if e.line is not None and len(e.line) > LINE_CHARS:
        return "the line is longer than the limit"
    return None


@dataclass
class Reading:
    """The events a log held, and the lines skipped, counted by why."""

    events: list[Event] = field(default_factory=list)
    skipped: Counter[str] = field(default_factory=Counter)


def _time(value: object) -> datetime:
    if not isinstance(value, str):
        raise EventError("a time is not text")
    return datetime.fromisoformat(value)


def _from_v1(body: dict[str, Any]) -> Event:
    """A version 1 line, read only when every field is there and nothing else is."""
    late = body.get("closed_late", False)
    if set(body) - {"closed_late"} != set(FIELDS) or not isinstance(late, bool):
        raise EventError("the fields are not version 1's")
    simple = {k: body[k] for k in FIELDS[2:] if k not in ("started", "duration_s")}
    # RULE: a stored blocker of an old name is read as its new name
    simple["blocker"] = OLD_BLOCKERS.get(simple["blocker"], simple["blocker"])
    read = Event(at=_time(body["at"]), started=_time(body["started"]), closed_late=late, **simple)
    if body["duration_s"] != read.duration_s:
        raise EventError("the duration does not match its times")
    return read


#: The reader for each version this bridge reads. A version 2 adds its reader and keeps 1's.
READERS: dict[int, Callable[[dict[str, Any]], Event]] = {1: _from_v1}


def _reader(body: dict[str, Any]) -> Callable[[dict[str, Any]], Event] | None:
    version = body.get("v")
    # RULE: a version that only compares equal to one is not read as one
    if type(version) is not int:
        return None
    return READERS.get(version)


def read(lines: Iterable[str]) -> Reading:
    """Every event in the lines; a line of another version or of no known shape is skipped and counted."""
    out = Reading()
    for text in lines:
        if not text.strip():
            continue
        try:
            body = json.loads(text)
        except ValueError:
            body = None
        if not isinstance(body, dict):
            out.skipped["unreadable"] += 1
            continue
        reader = _reader(body)
        # RULE: an unknown event version is skipped and counted
        if reader is None:
            out.skipped["version"] += 1
            continue
        try:
            out.events.append(reader(body))
        except (EventError, TypeError, ValueError):
            # RULE: an event line that does not fit its version is skipped and counted
            out.skipped["unreadable"] += 1
    return out
