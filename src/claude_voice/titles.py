"""Give each MCP tool its display title from one shared table.

The titles live in ``tool_titles.toml`` beside this file, so this repository and
its forks keep one list and each adds only the tools it introduces. This module
depends on nothing else in the package and can be copied as it is.

Use it in place of ``mcp.tool``::

    tool = titled(mcp, load())

    @tool(annotations=READ_ONLY)
    def health() -> dict: ...

A tool the table does not name is refused when it is added, so a new tool
cannot reach a client with only its code name.
"""

from __future__ import annotations

import tomllib
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

TABLE = Path(__file__).with_name("tool_titles.toml")


def load(path: Path = TABLE) -> dict[str, str]:
    """Tool name to title, read from the shared table."""
    with path.open("rb") as f:
        return {str(k): str(v) for k, v in tomllib.load(f).get("titles", {}).items()}


def titled(mcp: Any, table: Mapping[str, str]) -> Callable[..., Callable[[Callable[..., Any]], Any]]:
    """A drop-in for ``mcp.tool`` that adds the tool's title from ``table``, by function name."""

    def tool(**kwargs: Any) -> Callable[[Callable[..., Any]], Any]:
        def register(fn: Callable[..., Any]) -> Any:
            name = kwargs.get("name") or fn.__name__
            if "title" not in kwargs and name not in table:
                raise KeyError(f"no title for tool {name!r}; add it to {TABLE.name}")
            return mcp.tool(**{"title": table.get(name), **kwargs})(fn)

        return register

    return tool
