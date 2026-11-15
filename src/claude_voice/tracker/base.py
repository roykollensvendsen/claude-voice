"""The narrow interface every work tracker is reached through, and the three states.

An adapter, such as `github`, maps its tracker's own columns and facts onto three
states, by the owner's choice: moving, stopped, and waits on the owner. Nothing finer.

Only the read side is built. The three writes below are the interface the board
will need (tracker-board.md, step 2). Until then each refuses, so nothing here can
change a tracker by mistake. When they are built, every string they send goes
through the privacy gate first.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

State = Literal["waits_on_owner", "stopped", "moving"]
#: The states in the order they are told: what waits on the owner first, then what
#: has stopped, then what is moving.
STATES: tuple[State, ...] = ("waits_on_owner", "stopped", "moving")
#: How each state reads in a line.
LABELS: dict[State, str] = {"waits_on_owner": "waits on owner", "stopped": "stopped", "moving": "moving"}


@dataclass(frozen=True)
class Item:
    """One piece of pushed work as a tracker holds it.

    `component` is the public repository it belongs to, `needs` the one-line why of its
    state, and `moved` when it last changed, or None when the tracker does not say.
    """

    id: str
    title: str
    component: str
    state: State
    needs: str
    url: str
    moved: datetime | None


class ReadOnly(NotImplementedError):
    """A write was asked of a tracker that only reads."""


class Tracker(ABC):
    """A work tracker: its items, and later the three writes."""

    @abstractmethod
    def items(self, component: str | None = None) -> list[Item]:
        """Each open item, or only those of one component."""

    def open_item(self, component: str, title: str, body: str) -> Item:
        """Open a new item for a component. Not built yet: the board comes in step 2."""
        raise ReadOnly("opening an item is not built yet; this tracker only reads")

    def set_state(self, item: Item, state: State, needs: str | None = None) -> None:
        """Move an item to a state, with what it needs. Not built yet."""
        raise ReadOnly("changing an item's state is not built yet; this tracker only reads")

    def link_change(self, item: Item, pr: str) -> None:
        """Link an item to the pull request that does its work. Not built yet."""
        raise ReadOnly("linking a change is not built yet; this tracker only reads")
