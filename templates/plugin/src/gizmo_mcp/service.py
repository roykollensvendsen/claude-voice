"""The core: ask the gizmo, and word every answer for the voice.

It knows no MCP. Every answer is a dict with `success`, an `outcome` and a `say`, the
sentence the voice reads aloud as it is, and it has one even when the gizmo fails.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any

from gizmo_mcp.device import NotSetUpError, SlowDownError, UnreachableError

if TYPE_CHECKING:
    from gizmo_mcp.device import Gizmo

UNREACHABLE = "The gizmo isn't answering. Check that it is switched on and on the network."
NOT_SET_UP = "No real gizmo is set up yet, so nothing was asked."
BROKE = "That went wrong inside gizmo-mcp. Try again, and if it fails again, check its log."


class GizmoService:
    """What the tools ask, answered from one gizmo."""

    def __init__(self, gizmo: Gizmo) -> None:
        """Answer from `gizmo`, the fake or the real one."""
        self._gizmo = gizmo

    async def status(self) -> dict[str, Any]:
        """Whether the gizmo is on, and its level, as a sentence."""
        try:
            reading = await self._gizmo.read()
        except SlowDownError as slow:
            seconds = max(1, math.ceil(slow.seconds))
            return _failed("slow_down", f"The gizmo is busy; ask again in {seconds} seconds.")
        except UnreachableError:
            return _failed("unreachable", UNREACHABLE)
        except NotSetUpError:
            return _failed("not_set_up", NOT_SET_UP)
        # RULE: every answer has a sentence to say even when the gizmo fails
        except Exception:  # noqa: BLE001 - whatever broke, the voice gets a sentence
            return _failed("broke", BROKE)
        state = "on" if reading.on else "off"
        return {
            "success": True,
            "outcome": "read",
            "on": reading.on,
            "level": reading.level,
            "say": f"The gizmo is {state}, at level {reading.level}.",
        }


def _failed(outcome: str, say: str) -> dict[str, Any]:
    return {"success": False, "outcome": outcome, "say": say}
