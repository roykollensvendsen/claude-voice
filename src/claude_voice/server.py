"""The MCP face of the bridge: tools a voice assistant can call, over stdio or HTTP."""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from claude_agent_sdk import list_sessions as list_claude_sessions
from mcp.server import MCPServer
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from starlette.types import ASGIApp

from .approvals import ApprovalBroker, ApprovalNotFound
from .oauth import SCOPE, OAuthProvider, PublicClientMetadata
from .sessions import SessionBusy, SessionClosed, SessionManager
from .store import SessionNotFound, Store

MIN_TOKEN_CHARS = 32

INSTRUCTIONS = """\
Controls Claude Code sessions running on the user's own machine. The user is
usually speaking, often while driving, so keep what you read back short.
Typical flow: list_projects -> create_session -> send_task -> poll
session_recap until status is no longer "running" -> tell the user the result.
send_task returns immediately; Claude may work for minutes.
When a recap shows pending_approvals, read the tool and its input to the user
and call approve only if they clearly say yes; otherwise call deny.
"""


class ConfigError(RuntimeError):
    pass


@dataclass
class Config:
    root: Path
    db: Path
    transport: str
    host: str = "127.0.0.1"
    port: int = 8811
    token: str | None = None
    public_hosts: list[str] = field(default_factory=list)
    public_url: str = ""


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
        port=int(env.get("CLAUDE_VOICE_PORT", "8811")),
        token=env.get("CLAUDE_VOICE_TOKEN"),
        public_hosts=[h for h in env.get("CLAUDE_VOICE_PUBLIC_HOSTS", "").split(",") if h],
    )
    cfg.public_url = env.get("CLAUDE_VOICE_PUBLIC_URL", "").rstrip("/") or (
        f"https://{cfg.public_hosts[0]}" if cfg.public_hosts else f"http://127.0.0.1:{cfg.port}"
    )
    if transport == "http":
        if not cfg.token:
            raise ConfigError("CLAUDE_VOICE_TOKEN must be set to serve over HTTP")
        if len(cfg.token) < MIN_TOKEN_CHARS:
            raise ConfigError(f"CLAUDE_VOICE_TOKEN must be at least {MIN_TOKEN_CHARS} characters")
    return cfg


def build_server(manager: SessionManager, oauth: OAuthProvider | None = None) -> MCPServer:
    auth = None
    if oauth is not None:
        auth = AuthSettings(
            issuer_url=oauth.public_url,
            resource_server_url=f"{oauth.public_url}/mcp",
            client_registration_options=ClientRegistrationOptions(
                enabled=True, valid_scopes=[SCOPE], default_scopes=[SCOPE]
            ),
            required_scopes=[SCOPE],
            revocation_options=RevocationOptions(enabled=True),
            validate_token_resource=False,
        )
    mcp = MCPServer(
        "claude-voice", instructions=INSTRUCTIONS, auth_server_provider=oauth, auth=auth
    )
    if oauth is not None:
        mcp.custom_route("/oauth/consent", methods=["GET"])(oauth.consent_page)
        mcp.custom_route("/oauth/consent", methods=["POST"])(oauth.consent_submit)
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

    @mcp.tool()
    def list_pending_approvals(session_id: str | None = None) -> dict[str, Any]:
        """Tool calls Claude is waiting to be allowed to make. Read each one to the user."""
        if manager.approvals is None:
            return {"approvals": []}
        return {"approvals": manager.approvals.pending(session_id)}

    @mcp.tool()
    def approve(approval_id: str) -> dict[str, Any]:
        """Allow one pending tool call. Only after the user has clearly said yes to it."""
        return _answer(lambda b: b.approve(approval_id))

    @mcp.tool()
    def deny(approval_id: str, reason: str | None = None) -> dict[str, Any]:
        """Refuse one pending tool call; the reason is passed on to Claude."""
        return _answer(lambda b: b.deny(approval_id, reason))

    def _answer(fn) -> dict[str, Any]:
        if manager.approvals is None:
            raise ToolError("Approvals are not enabled on this bridge")
        try:
            return fn(manager.approvals)
        except ApprovalNotFound:
            raise ToolError("No such pending approval; it may have expired") from None

    return mcp


def build_app(server: MCPServer, public_hosts: list[str] | None = None) -> ASGIApp:
    """HTTP app. Auth (OAuth plus the static token) comes from the server's provider."""
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
    return PublicClientMetadata(server.streamable_http_app(transport_security=security))


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="claude-voice", description=__doc__)
    parser.add_argument("--transport", choices=["stdio", "http"], default="stdio")
    args = parser.parse_args(argv)
    try:
        cfg = load_config(os.environ, args.transport)
    except ConfigError as exc:
        sys.exit(f"claude-voice: {exc}")

    store = Store(cfg.db)
    manager = SessionManager(store, project_root=cfg.root, approvals=ApprovalBroker(store))
    if cfg.transport == "stdio":
        build_server(manager).run("stdio")
        return

    import uvicorn

    token = cfg.token or ""
    # The token doubles as the login secret on the OAuth consent page and as a
    # static bearer token for local MCP clients.
    oauth = OAuthProvider(store, login_secret=token, public_url=cfg.public_url, static_token=token)
    app = build_app(build_server(manager, oauth=oauth), cfg.public_hosts)
    uvicorn.run(app, host=cfg.host, port=cfg.port, log_level="info")
