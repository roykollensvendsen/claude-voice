"""The one place that knows which operating system the plugin runs on.

Everything else asks `current()`. Linux is the only platform today; another one is a new
module beside `linux.py`, chosen here, and nothing outside this folder changes.
`tests/test_platform_boundary.py` refuses an OS path or command anywhere else.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from gizmo_mcp.platform.linux import Linux

if TYPE_CHECKING:
    from gizmo_mcp.platform.base import Platform


def current() -> Platform:
    """The platform this process runs on."""
    return Linux()
