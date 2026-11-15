"""Linux, by the XDG base directory rules."""

from __future__ import annotations

import os
import pathlib


class Linux:
    """The folders as a Linux desktop or server keeps them."""

    def config_home(self) -> pathlib.Path:
        """`$XDG_CONFIG_HOME`, or `~/.config` when it is unset or empty."""
        return pathlib.Path(os.environ.get("XDG_CONFIG_HOME") or pathlib.Path.home() / ".config")
