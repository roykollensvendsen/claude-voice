"""The gate: the one way a collector's raw item becomes an event in the friction log.

It lets fields through by an allow-list, so personal data and secrets stay out:

- the session and the repo are the bridge's own, from its programme files, never the item's;
- the source is the collector's registered name, given by the caller;
- `what` is a path the repo's git knows, a rule name, or a word in the collector's
  own vocabulary. Anything else becomes the collector's catch-all word, or nothing
  when it has none, and is counted per source;
- the line is one line of at most 120 characters, with token-shaped text blanked,
  and it is dropped whole when it holds private data;
- the key must be plain and hold no private data, since it goes into the id;
- the id is the source, the collector's key and the friction's start to the second
  in UTC, so a second stall of one session is a new id, and a close matches its
  open whatever offset each clock gave.

The scan for private data is the one the voice repositories use on their own
commits, as patterns, plus the owner's private words, which the caller reads from
the files outside every repository. A private word must be at least four letters
to be matched; a shorter one would match ordinary text, so it is ignored.
Coordinates are caught as a pair, latitude and longitude, with three or more
decimals; a lone exact number is not.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime

from claude_voice.restarts import one_line
from claude_voice.workflow.core.event import Event, EventError
from claude_voice.workflow.core.protocol import Raw, Where

#: What a blanked token reads as.
BLANK = "[blanked]"
#: A private word shorter than this would match ordinary text.
MIN_WORD = 4
#: An id key: plain characters, no space, short enough to read.
KEY = re.compile(r"[A-Za-z0-9._#/@+-][A-Za-z0-9._#/@+:-]{0,119}")
TOKENS = (
    re.compile(r"\bhttps?://\S*\?\S*"),  # a URL with a query string, which may carry a code or key
    re.compile(r"\bBearer\s+\S+"),
    re.compile(r"\b(?:sk-|ghp_|gho_|github_pat_)[\w-]+"),
    re.compile(r"(?<![\w+/-])(?=[\w+/-]*\d)[\w+/-]{32,}={0,2}"),  # a base64 run with a digit in it
    re.compile(r"(?<![0-9A-Fa-f])[0-9A-Fa-f]{32,}(?![0-9A-Fa-f])"),  # a hex run
)
PRIVATE = (
    re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"),  # an email address
    re.compile(r"-?\b\d{1,2}\.\d{3,}\s*,\s*-?\d{1,3}\.\d{3,}\b"),  # coordinates more exact than two decimals
    re.compile(r"\bE[A-Z](?=[A-Z0-9]{0,5}\d)[A-Z0-9]{6}\b"),  # an Easee charger ID
    re.compile(r"(?<![\d.])(?:10\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01])|192\.168)\.\d{1,3}\.\d{1,3}(?![\d.])"),
    re.compile(
        r"\b[A-ZÆØÅ][a-zæøå]+(?:gata|gaten|gate|veien|vegen|vei|veg|torget|plassen|allé|alleen)\s+\d{1,4}[A-Za-z]?\b"
    ),
)


@dataclass(frozen=True)
class Known:
    """What a `what` may be, and the owner's private words, all read by the caller."""

    paths: Mapping[str, frozenset[str]]  # per repo, the paths its git knows
    rules: frozenset[str]
    private: frozenset[str]


@dataclass(frozen=True)
class Setting:
    """Who calls the gate and what it checks against: the collector's name and words, from
    the bridge, and the time to stamp."""

    source: str
    known: Known
    programmes: Mapping[str, Where]
    now: datetime
    vocabulary: frozenset[str] = frozenset()
    catch_all: str | None = None


@dataclass(frozen=True)
class Refused:
    """An item the gate kept out, and why, by field. The reason never quotes the item."""

    reason: str


@dataclass
class Batch:
    """What one call of a collector became: its events, its refusals by reason, and the
    `what`s that were none of its known words, counted per source."""

    events: list[Event] = field(default_factory=list)
    refused: Counter[str] = field(default_factory=Counter)
    unknown_what: Counter[str] = field(default_factory=Counter)


def blank_tokens(text: str) -> str:
    """The text with everything shaped like a token, a key or a signed link blanked."""
    for shape in TOKENS:
        text = shape.sub(BLANK, text)
    return text


def private(text: str, words: frozenset[str]) -> bool:
    """Whether the text holds an email, exact coordinates, a charger ID, a home address or a private word."""
    lowered = text.lower()
    if any(shape.search(text) for shape in PRIVATE):
        return True
    # RULE: a private word shorter than four letters is ignored
    listed = [word.lower() for word in words if len(word) >= MIN_WORD]
    return any(word in lowered for word in listed)


def _line(text: str | None, words: frozenset[str]) -> str | None:
    if text is None:
        return None
    flat = " ".join(text.split())
    # RULE: a token shaped text is blanked
    flat = blank_tokens(flat)
    # RULE: a line with private data is dropped
    if private(flat, words):
        return None
    # RULE: an event line is cut to the limit
    return one_line(flat)


def _what(
    what: str | None, repo: str, known: Known, vocabulary: frozenset[str], catch_all: str | None
) -> tuple[str | None, bool]:
    """The `what` kept, and whether it was none of the known words."""
    if what is None:
        return None, False
    allowed = known.paths.get(repo, frozenset()) | known.rules
    # RULE: a word in the collectors own vocabulary is a known what
    allowed = allowed | vocabulary
    # RULE: what is only a known path rule or word
    if what in allowed:
        return what, False
    # RULE: an unknown what from a collector with a catch all becomes its catch all
    return catch_all, True


def _start(raw: Raw, now: datetime) -> datetime | None:
    # RULE: an open or closed event needs its start
    if raw.started is None and raw.state != "point":
        return None
    return raw.started or now


def _key_fault(key: str, words: frozenset[str]) -> str | None:
    # RULE: an event key is plain
    if not KEY.fullmatch(key) or blank_tokens(key) != key:
        return "the key is not plain text"
    # RULE: an event key holds no private data
    if private(key, words):
        return "the key holds private data"
    return None


def _id(source: str, key: str, started: datetime) -> str:
    # RULE: an event id writes its start in utc
    moment = started.astimezone(UTC)
    # RULE: an event id carries its start to the second
    return f"{source}:{key}:{moment.strftime('%Y-%m-%dT%H:%M:%SZ')}"


def _gate(raw: Raw, setting: Setting) -> tuple[Event | Refused, bool]:
    """The event, or why not, and whether its `what` was none of the known words."""
    known = setting.known
    # RULE: an event from a place the bridge does not know is refused
    where = setting.programmes.get(raw.about)
    if where is None:
        return Refused("the item names no place the bridge's programmes know"), False
    fault = _key_fault(raw.key, known.private)
    if fault:
        return Refused(fault), False
    started = _start(raw, setting.now)
    if started is None:
        return Refused("an open or closed item has no start"), False
    started, now = started.replace(microsecond=0), setting.now.replace(microsecond=0)  # as the line keeps them
    what, unknown = _what(raw.what, where.repo, known, setting.vocabulary, setting.catch_all)
    try:
        made = Event(
            at=now,
            source=setting.source,
            id=_id(setting.source, raw.key, started),
            session=where.session,
            repo=where.repo,
            kind=raw.kind,
            blocker=raw.blocker,
            state=raw.state,
            started=started,
            what=what,
            line=_line(raw.line, known.private),
            closed_late=raw.closed_late,
        )
    except EventError as error:
        return Refused(str(error)), False
    return made, unknown


def gate(raw: Raw, setting: Setting) -> Event | Refused:
    """The event this item becomes, stamped at the setting's `now`, or why it was refused."""
    return _gate(raw, setting)[0]


def gate_all(raws: Iterable[Raw], setting: Setting) -> Batch:
    """Every item of one collector's call through the gate, with what was refused and unknown counted."""
    batch = Batch()
    for raw in raws:
        out, unknown = _gate(raw, setting)
        if isinstance(out, Refused):
            batch.refused[out.reason] += 1
            continue
        batch.events.append(out)
        # RULE: a what from outside the collectors words is counted per source
        if unknown:
            batch.unknown_what[setting.source] += 1
    return batch
