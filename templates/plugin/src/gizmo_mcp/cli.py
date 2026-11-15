"""Command line for gizmo-mcp: serve the tools, or say the gizmo's status once."""

from __future__ import annotations

import argparse
import asyncio
import functools
import sys

from gizmo_mcp import settings
from gizmo_mcp.device import Unconnected
from gizmo_mcp.fake import FakeGizmo
from gizmo_mcp.service import GizmoService

#: Every verb this command has.
COMMANDS: tuple[str, ...] = ("serve", "status")
#: Help is laid out at one width on every terminal, so a page's copy of it stays true.
HELP = functools.partial(argparse.HelpFormatter, width=100)


def main(argv: list[str] | None = None) -> int:
    """Parse arguments and dispatch. Returns a process exit code."""
    parser = argparse.ArgumentParser(prog="gizmo-mcp", description=__doc__, formatter_class=HELP)
    verbs = parser.add_subparsers(dest="command", required=True)
    serve = verbs.add_parser("serve", formatter_class=HELP, help="serve the tools over stdio, for a voice session")
    status = verbs.add_parser("status", formatter_class=HELP, help="say the gizmo's status, as the voice hears it")
    for verb in (serve, status):
        verb.add_argument("--simulate", action="store_true", help="answer from the made-up gizmo")
    args = parser.parse_args(argv)
    try:
        configured = settings.load()
    except settings.SettingsError as broken:
        sys.stderr.write(f"{broken}\n")
        return 2
    simulate = args.simulate or configured.simulate
    service = GizmoService(FakeGizmo() if simulate else Unconnected())

    # The MCP server is a slow import, and only a working command needs it.
    from mcp.types import CallToolResult  # noqa: PLC0415

    from gizmo_mcp.server import build_server  # noqa: PLC0415

    server = build_server(service, simulated=simulate)
    if args.command == "status":
        # Through the tool itself, so this says exactly what the voice would hear.
        result = asyncio.run(server.call_tool("gizmo_status", {}))
        answer = result.structured_content if isinstance(result, CallToolResult) else None
        sys.stdout.write(str((answer or {}).get("say", "")) + "\n")
        return 0
    server.run("stdio")  # pragma: no cover - runs until stdin closes
    return 0  # pragma: no cover


if __name__ == "__main__":
    sys.exit(main())
