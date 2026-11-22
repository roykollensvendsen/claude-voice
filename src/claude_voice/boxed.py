"""Boxed: how the approver's claude is started, so that it can do nothing but its job.

The approver reads boxes from other sessions, and their text is not to be trusted. So it
starts with no built-in tools (no shell, no agents, no background tasks, no file tools), with
none of the owner's settings files, and with the bridge as its only MCP server. Its settings
allow only the two approver tools and load only the hook the bridge gives it.

Known limits. With no settings files, the owner's own deny rules do not load either; the
approver is confined by having no tools and by its token, which opens only the two approver
tools on the bridge. Its token sits in its environment, which the same Unix user can read,
and the hook it runs inherits it (approver-v1, v1.7).
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

from mcp.shared.exceptions import MCPError
from mcp.types import INVALID_REQUEST, CallToolResult, ListToolsResult, TextContent

#: The bridge's name as the approver's MCP server; its tools are mcp__<SERVER>__<tool>.
SERVER = "claude-voice"
TOOLS = ("approver_boxes", "approver_answer")
#: The client name the bridge gives the approver's own token.
CLIENT = "approver"
#: What a message with the approver's token may ask, besides notifications.
OPEN = ("initialize", "ping", "tools/list", "tools/call")
#: Pinned here, since the owner's model setting lives in a settings file the approver skips.
MODEL = "claude-opus-5-5"
#: The environment variable the approver's token is read from; the token is never in argv.
TOKEN_VAR = "CLAUDE_VOICE_APPROVER_TOKEN"


def mcp_config(port: int) -> str:
    """The approver's only MCP server: the bridge on this computer, with the token's name."""
    # RULE: the approver reaches only the bridge
    server = {
        "type": "http",
        "url": f"http://127.0.0.1:{port}/mcp",
        "headers": {"Authorization": f"Bearer ${{{TOKEN_VAR}}}"},
    }
    return json.dumps({"mcpServers": {SERVER: server}})


def settings(hooks: dict[str, Any]) -> str:
    """The approver's whole settings: the bridge's hook, and an allow for its two tools only."""
    # RULE: the approver may call only its two tools
    allow = [f"mcp__{SERVER}__{tool}" for tool in TOOLS]
    return json.dumps({"hooks": hooks, "permissions": {"allow": allow}})


def claude_args(cli: str, name: str, port: int, hooks: dict[str, Any], prompt: str) -> list[str]:
    """The command line of the approver's claude, from its absolute path."""
    return [
        cli,
        "--name",
        name,
        "--model",
        MODEL,
        "--strict-mcp-config",
        "--mcp-config",
        mcp_config(port),
        # RULE: the approver has no tools of its own
        "--tools",
        "",
        # RULE: the approver loads none of the owners settings files
        "--setting-sources",
        "",
        "--settings",
        settings(hooks),
        "--",
        prompt,
    ]


def _client(ctx: Any) -> str | None:
    """The client of the token this one message came with, from its own HTTP request."""
    user = (getattr(getattr(ctx, "request", None), "scope", None) or {}).get("user")
    token = getattr(user, "access_token", None)
    return getattr(token, "client_id", None)


async def gate(ctx: Any, call_next: Callable[[Any], Awaitable[Any]]) -> Any:
    """MCP middleware: a message that came with the approver's token reaches only its two
    tools, and lists only those. Any other login passes through untouched."""
    if _client(ctx) != CLIENT:
        return await call_next(ctx)
    # RULE: the approvers token asks nothing but tools
    if ctx.method not in OPEN and not ctx.method.startswith("notifications/"):
        raise MCPError(INVALID_REQUEST, "Not open to the approver.")
    tool = str((ctx.params if isinstance(ctx.params, dict) else {}).get("name", ""))
    # RULE: the approvers token opens only its two tools
    if ctx.method == "tools/call" and tool not in TOOLS:
        return CallToolResult(content=[TextContent(type="text", text="Not open to the approver.")], is_error=True)
    result = await call_next(ctx)
    if ctx.method == "tools/list" and isinstance(result, ListToolsResult):
        # RULE: the approver sees only its two tools
        result.tools = [t for t in result.tools if t.name in TOOLS]
    return result
