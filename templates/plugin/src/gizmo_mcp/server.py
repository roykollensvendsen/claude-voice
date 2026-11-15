"""The MCP server: the gizmo's tools, over stdio, for a voice session.

It only puts the core together and names the tools; every answer comes from the service.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

if TYPE_CHECKING:
    from gizmo_mcp.service import GizmoService

#: The Claude app shows a tool by the start of its description, cut at this many characters.
FIRST_LINE_MAX = 78
INSTRUCTIONS = "A made-up gizmo. Read each answer's 'say' aloud as it is."
SIMULATED = "SIMULATED gizmo, not a real one: say so. "
STATUS_DESCRIPTION = (
    "Is the gizmo on, and at what level?\n"
    "Whether the gizmo is switched on, and its level from 0 to 100. It only reads; it changes nothing."
)
#: Every tool, by name. The manifest lists the same, and each has a test file of its own.
TOOLS = ("gizmo_status",)
READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True)


def build_server(service: GizmoService, *, simulated: bool = False) -> MCPServer[Any]:
    """The MCP server with the gizmo's tools, answering from `service`."""
    server: MCPServer[Any] = MCPServer(
        name="gizmo-simulated" if simulated else "gizmo",
        instructions=(SIMULATED + INSTRUCTIONS) if simulated else INSTRUCTIONS,
    )

    def marked(answer: dict[str, Any]) -> dict[str, Any]:
        answer["simulated"] = simulated
        if simulated:
            answer["say"] = "Simulated: " + answer["say"]
        return answer

    @server.tool(annotations=READ_ONLY, description=STATUS_DESCRIPTION)
    async def gizmo_status() -> dict[str, Any]:
        return marked(await service.status())

    return server
