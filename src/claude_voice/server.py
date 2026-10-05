"""The MCP face of the bridge: tools a voice assistant can call, over stdio or HTTP."""

from __future__ import annotations

import argparse
import hmac
import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from claude_agent_sdk import list_sessions as list_claude_sessions
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from starlette.types import ASGIApp, Receive, Scope, Send

from .sessions import SessionBusy, SessionClosed, SessionManager
from .store import SessionNotFound, Store

MIN_TOKEN_CHARS = 32

INSTRUCTIONS = """\
Controls Claude Code sessions running on the user's own machine. The user is
usually speaking, often while driving, so keep what you read back short.
Typical flow: list_projects -> create_session -> send_task -> poll
session_recap until status is no longer "running" -> tell the user the result.
send_task returns immediately; Claude may work for minutes.
"""


class ConfigError(RuntimeError):
    pass


@dataclass
class Config:
    root: Path
    db: Path
    transport: str
    host: str = "127.0.0.1"
    port: int = 8765
    token: str | None = None
    public_hosts: list[str] = field(default_factory=list)


def load_config(env: Mapping[str, str], transport: str) -> Config:
    if env.get("ANTHROPIC_API_KEY") and env.get("CLAUDE_VOICE_ALLOW_API_KEY") != "1":
        raise ConfigError(
            "ANTHROPIC_API_KEY is set, so Claude would bill the API instead of your "
            "subscription. Unset it, or set CLAUDE_VOICE_ALLOW_API_KEY=1 if that is intended."
        )
    root = Path(env.get("CLAUDE_VOICE_ROOT", "~/src")).expanduser()
    if not root.is_dir():
        raise ConfigError(f"CLAUDE_VOICE_ROOT is not a directory: {root}")
    cfg = Config(
        root=root,
        db=Path(env.get("CLAUDE_VOICE_DB", "~/.local/state/claude-voice/bridge.db")).expanduser(),
        transport=transport,
        host=env.get("CLAUDE_VOICE_HOST", "127.0.0.1"),
        port=int(env.get("CLAUDE_VOICE_PORT", "8765")),
        token=env.get("CLAUDE_VOICE_TOKEN"),
        public_hosts=[h for h in env.get("CLAUDE_VOICE_PUBLIC_HOSTS", "").split(",") if h],
    )
    if transport == "http":
        if not cfg.token:
            raise ConfigError("CLAUDE_VOICE_TOKEN must be set to serve over HTTP")
        if len(cfg.token) < MIN_TOKEN_CHARS:
            raise ConfigError(f"CLAUDE_VOICE_TOKEN must be at least {MIN_TOKEN_CHARS} characters")
    return cfg


def build_server(manager: SessionManager) -> MCPServer:
    mcp = MCPServer("claude-voice", instructions=INSTRUCTIONS)
    store = manager.store

    def known(session_id: str) -> None:
        try:
            store.get_session(session_id)
        except SessionNotFound:
            raise ToolError(f"No session with id {session_id}") from None

    @mcp.tool()
    def list_projects() -> dict[str, Any]:
        """List the project directories Claude can be started in."""
        names = sorted(
            p.name for p in manager.root.iterdir() if p.is_dir() and not p.name.startswith(".")
        )
        return {"root": str(manager.root), "projects": names}

    @mcp.tool()
    def create_session(project: str, label: str | None = None) -> dict[str, Any]:
        """Start a new Claude Code session in a project (a name from list_projects)."""
        try:
            return manager.create(project, label)
        except ValueError as exc:
            raise ToolError(str(exc)) from None

    @mcp.tool()
    def list_sessions(
        status: str | None = None, project: str | None = None, limit: int = 20
    ) -> dict[str, Any]:
        """List bridge sessions, most recently active first."""
        try:
            project_path = str(manager.resolve_project(project)) if project else None
        except ValueError as exc:
            raise ToolError(str(exc)) from None
        rows = store.list_sessions(project_path, status, min(max(limit, 1), 100))
        return {"sessions": rows}

    @mcp.tool()
    async def send_task(session_id: str, prompt: str) -> dict[str, Any]:
        """Give Claude a task or follow-up. Returns at once; poll session_recap for progress."""
        known(session_id)
        try:
            return await manager.send(session_id, prompt)
        except (SessionBusy, SessionClosed, ValueError) as exc:
            msg = str(exc) if not isinstance(exc, SessionClosed) else "That session is closed."
            raise ToolError(msg) from None

    @mcp.tool()
    def session_recap(session_id: str) -> dict[str, Any]:
        """Short status of one session: running or not, last prompt, latest words, result."""
        known(session_id)
        return manager.recap(session_id)

    @mcp.tool()
    def get_messages(session_id: str, after: int = 0, limit: int = 50) -> dict[str, Any]:
        """Detailed event log of a session. Pass next_after back as `after` to page on."""
        known(session_id)
        events = store.events(session_id, after=max(after, 0), limit=min(max(limit, 1), 200))
        return {"events": events, "next_after": events[-1]["seq"] if events else after}

    @mcp.tool()
    def recent_activity(since_minutes: int = 1440, limit: int = 100) -> dict[str, Any]:
        """Everything that happened across all sessions in the last N minutes."""
        since = manager.clock() - max(since_minutes, 1) * 60
        return {"events": store.recent_events(since, limit=min(max(limit, 1), 500))}

    @mcp.tool()
    def fleet_recap(since_minutes: int = 1440) -> dict[str, Any]:
        """One recap per session active in the last N minutes: 'what have my agents done?'"""
        return manager.fleet_recap(max(since_minutes, 1))

    @mcp.tool()
    async def cancel(session_id: str) -> dict[str, Any]:
        """Stop what Claude is doing in a session. The session can be used again afterwards."""
        known(session_id)
        return await manager.cancel(session_id)

    @mcp.tool()
    def close_session(session_id: str) -> dict[str, Any]:
        """Retire a session. Its history is kept."""
        known(session_id)
        try:
            return manager.close(session_id)
        except SessionBusy as exc:
            raise ToolError(str(exc)) from None

    @mcp.tool()
    def list_claude_conversations(project: str, limit: int = 10) -> dict[str, Any]:
        """Claude Code conversations on this machine for a project, including terminal ones."""
        try:
            path = manager.resolve_project(project)
        except ValueError as exc:
            raise ToolError(str(exc)) from None
        found = list_claude_sessions(directory=str(path), limit=min(max(limit, 1), 50))
        return {
            "conversations": [
                {
                    "claude_session_id": c.session_id,
                    "summary": c.custom_title or c.summary,
                    "first_prompt": c.first_prompt,
                    "git_branch": c.git_branch,
                    "last_modified": c.last_modified,
                }
                for c in found
            ]
        }

    @mcp.tool()
    def attach_conversation(
        claude_session_id: str, project: str, label: str | None = None
    ) -> dict[str, Any]:
        """Continue an existing Claude Code conversation (from list_claude_conversations)."""
        try:
            return manager.attach(claude_session_id, project, label)
        except ValueError as exc:
            raise ToolError(str(exc)) from None

    return mcp


class BearerAuth:
    """Reject every HTTP request that lacks `Authorization: Bearer <token>`."""

    def __init__(self, app: ASGIApp, token: str, open_paths: frozenset[str]) -> None:
        self.app = app
        self.expected = f"Bearer {token}".encode()
        self.open_paths = open_paths

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["path"] in self.open_paths:
            await self.app(scope, receive, send)
            return
        given = dict(scope["headers"]).get(b"authorization", b"")
        if not hmac.compare_digest(given, self.expected):
            await send(
                {
                    "type": "http.response.start",
                    "status": 401,
                    "headers": [
                        (b"www-authenticate", b'Bearer realm="claude-voice"'),
                        (b"content-type", b"text/plain"),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": b"unauthorized"})
            return
        await self.app(scope, receive, send)


def build_app(server: MCPServer, token: str, public_hosts: list[str] | None = None) -> ASGIApp:
    from starlette.responses import PlainTextResponse

    @server.custom_route("/healthz", methods=["GET"])
    async def healthz(request):  # noqa: ARG001
        return PlainTextResponse("ok")

    hosts = ["127.0.0.1:*", "localhost:*", "[::1]:*", *(public_hosts or [])]
    security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=hosts,
        allowed_origins=[f"https://{h}" for h in public_hosts or []]
        + ["http://127.0.0.1:*", "http://localhost:*"],
    )
    app = server.streamable_http_app(transport_security=security)
    return BearerAuth(app, token, open_paths=frozenset({"/healthz"}))


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="claude-voice", description=__doc__)
    parser.add_argument("--transport", choices=["stdio", "http"], default="stdio")
    args = parser.parse_args(argv)
    try:
        cfg = load_config(os.environ, args.transport)
    except ConfigError as exc:
        sys.exit(f"claude-voice: {exc}")

    manager = SessionManager(Store(cfg.db), project_root=cfg.root)
    server = build_server(manager)
    if cfg.transport == "stdio":
        server.run("stdio")
        return

    import uvicorn

    app = build_app(server, cfg.token or "", cfg.public_hosts)
    uvicorn.run(app, host=cfg.host, port=cfg.port, log_level="info")
