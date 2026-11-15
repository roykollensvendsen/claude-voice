"""What the plugin asks of the operating system."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    import pathlib


class Platform(Protocol):
    """The folders a plugin keeps its files in."""

    def config_home(self) -> pathlib.Path:
        """The folder that holds each program's own config folder."""
        ...
