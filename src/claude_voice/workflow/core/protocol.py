"""What a collector is, what it is given, and what it yields.

A collector reads one source and yields `Raw` items. It never writes the log: the
core gates each item, stamps it, appends it, and only then saves the collector's
state. An item has no field for its session, its repo or its source, so a
collector cannot set them; it names a place by `about`, which the gate looks up in
the bridge's programmes.

A collector may also name a `vocabulary`: a frozenset of its own words, such as
reviewer names, that the gate accepts as `what` from that collector alone. Each
word is short and plain. A collector with a vocabulary also names its own
`catch_all`, one of those words, such as `other-reviewer` or `other-tool`. The
gate turns any `what` it does not know from that collector into the catch-all,
so a word the collector does not know is never free text.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping, MutableMapping
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable

#: A collector's name, which becomes each of its events' `source`.
NAME = re.compile(r"[a-z][a-z0-9_]{0,31}")
#: One word of a collector's vocabulary.
WORD = re.compile(r"[a-z0-9-]{1,40}")


@dataclass(frozen=True)
class Where:
    """A session and its repo, as the bridge's programme files name them."""

    session: str
    repo: str


@dataclass(frozen=True)
class Raw:
    """One thing a collector saw, before the gate.

    `key` is the collector's own stable key for the friction point; `about` names
    the place, to be looked up in the programmes. `started` is when the friction
    began, and an open or closed item needs it.
    """

    key: str
    about: str
    kind: str
    blocker: str
    state: str
    started: datetime | None = None
    what: str | None = None
    line: str | None = None
    closed_late: bool = False


@dataclass(frozen=True)
class Context:
    """What the core gives a collector on each call."""

    state: MutableMapping[str, str]  # this collector's own, kept across restarts
    now: datetime
    programmes: Mapping[str, Where]  # read-only: the places the bridge knows
    log_seen: Callable[[str, str], bool]  # whether an id in a state is already in the log


@runtime_checkable
class Collector(Protocol):
    """One source's collector. It may also have `vocabulary: frozenset[str]` and `catch_all: str`."""

    name: str  # unique; becomes `source`
    every_s: int  # how often the core calls it, in seconds

    def collect(self, ctx: Context) -> Iterable[Raw]:
        """What the source shows since the last call."""
        ...


def vocabulary_of(collector: Collector) -> frozenset[str]:
    """The collector's own words, or none."""
    words: object = getattr(collector, "vocabulary", frozenset())
    # RULE: a vocabulary is a frozen set
    if not isinstance(words, frozenset):
        raise ValueError(f"the collector {collector.name}'s vocabulary is not a frozenset")
    return words


def catch_all_of(collector: Collector) -> str | None:
    """The collector's word for anything outside its vocabulary, or None."""
    word: object = getattr(collector, "catch_all", None)
    return word if isinstance(word, str) else None


def _check_vocabulary(collector: Collector) -> None:
    words = vocabulary_of(collector)
    catch_all = catch_all_of(collector)
    for word in sorted(words):
        # RULE: a vocabulary word is short and plain
        if not WORD.fullmatch(word):
            raise ValueError(f"a word in {collector.name}'s vocabulary is not 1-40 of a-z, 0-9 and -")
    # RULE: a vocabulary holds its own catch all
    if (words or catch_all is not None) and catch_all not in words:
        raise ValueError(f"{collector.name} needs a catch_all that is one of its vocabulary's words")


def check(collectors: Iterable[Collector]) -> None:
    """Refuse a list of collectors that would mix up their events, naming what to fix."""
    seen: set[str] = set()
    for collector in collectors:
        # RULE: a collector name is plain
        if not NAME.fullmatch(collector.name):
            raise ValueError("a collector's name is not 1-32 of a-z, 0-9 and _, starting with a letter")
        # RULE: a collector name is unique
        if collector.name in seen:
            raise ValueError(f"the collector name {collector.name} is listed twice; rename one")
        # RULE: a collector runs at most once a second
        if collector.every_s < 1:
            raise ValueError(f"the collector {collector.name}'s every_s is below one second")
        seen.add(collector.name)
        _check_vocabulary(collector)
