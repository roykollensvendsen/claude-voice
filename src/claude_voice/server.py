"""The MCP face of the bridge: tools a voice assistant can call, over stdio or HTTP."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from claude_agent_sdk import SDKSessionInfo
from claude_agent_sdk import list_sessions as list_claude_sessions
from mcp.server import MCPServer
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from starlette.types import ASGIApp

from . import transcripts
from .approvals import ApprovalBroker, ApprovalNotFound
from .oauth import SCOPE, OAuthProvider, PublicClientMetadata
from .sessions import SessionBusy, SessionClosed, SessionManager
from .store import SessionNotFound, Store

MIN_TOKEN_CHARS = 32
READ_ONLY = ToolAnnotations(read_only_hint=True)
# Claude Code keeps one JSON file per running session here.
LIVE_DIR = Path("~/.claude/sessions").expanduser()

INSTRUCTIONS = """\
Controls Claude Code sessions running on the user's own machine. The user is
usually speaking, often while driving, so keep what you read back short.
Typical flow: list_projects -> create_session -> send_task -> poll
session_recap until status is no longer "running" -> tell the user the result.
send_task returns immediately; Claude may work for minutes.
To see what is running on the machine (including terminal sessions), call
list_active_sessions; to continue one, attach_conversation with its id.
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


log = logging.getLogger("claude_voice")


async def log_requests(ctx, call_next):
    """Log each MCP method (and tool name) so the journal shows what a client asked for."""
    params = ctx.params if isinstance(ctx.params, dict) else {}
    what = f"{ctx.method} {params.get('name', '')}".rstrip()
    try:
        result = await call_next(ctx)
    except Exception as exc:
        log.info("mcp %s -> %s", what, type(exc).__name__)
        raise
    if ctx.method == "tools/call":
        failed = getattr(result, "is_error", None) or (
            isinstance(result, dict) and result.get("isError")
        )
        log.info("mcp %s -> %s", what, "error" if failed else "ok")
    elif ctx.request_id is not None:
        log.info("mcp %s", what)
    return result


def build_server(
    manager: SessionManager,
    oauth: OAuthProvider | None = None,
    conversations: Callable[..., list[SDKSessionInfo]] = list_claude_sessions,
    live_dir: Path = LIVE_DIR,
    deliver: Callable[[str, str], Awaitable[dict[str, Any]]] = transcripts.deliver,
    read_transcript: Callable[[str, str | None], list[Any]] = transcripts.read_transcript,
) -> MCPServer:
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
        "claude-voice",
        instructions=INSTRUCTIONS,
        auth_server_provider=oauth,
        auth=auth,
        middleware=[log_requests],
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

    @mcp.tool(annotations=READ_ONLY)
    def list_projects() -> dict[str, Any]:
        """List the project directories Claude can be started in."""
        names = sorted(
            p.name for p in manager.root.iterdir() if p.is_dir() and not p.name.startswith(".")
        )
        return {"root": str(manager.root), "projects": names}

    @mcp.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False))
    def create_session(project: str, label: str | None = None) -> dict[str, Any]:
        """Start a new Claude Code session in a project (a name from list_projects)."""
        try:
            return manager.create(project, label)
        except ValueError as exc:
            raise ToolError(str(exc)) from None

    @mcp.tool(annotations=READ_ONLY)
    def list_sessions(
        status: str | None = None, project: str | None = None, limit: int = 20
    ) -> dict[str, Any]:
        """List sessions: `sessions` are those started through this bridge;
        `running_claude_code_sessions` are all Claude Code sessions running on the machine
        right now (also those opened in a terminal)."""
        try:
            project_path = str(manager.resolve_project(project)) if project else None
        except ValueError as exc:
            raise ToolError(str(exc)) from None
        rows = store.list_sessions(project_path, status, min(max(limit, 1), 100))
        return {"sessions": rows, "running_claude_code_sessions": running()}

    @mcp.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=True))
    async def send_task(session_id: str, prompt: str) -> dict[str, Any]:
        """Give Claude a task or follow-up. Returns at once; poll session_recap for progress."""
        known(session_id)
        try:
            return await manager.send(session_id, prompt)
        except (SessionBusy, SessionClosed, ValueError) as exc:
            msg = str(exc) if not isinstance(exc, SessionClosed) else "That session is closed."
            raise ToolError(msg) from None

    @mcp.tool(annotations=READ_ONLY)
    def session_recap(session_id: str) -> dict[str, Any]:
        """Short status of one session: running or not, last prompt, latest words, result."""
        known(session_id)
        return manager.recap(session_id)

    @mcp.tool(annotations=READ_ONLY)
    def get_messages(session_id: str, after: int = 0, limit: int = 50) -> dict[str, Any]:
        """Detailed event log of a session. Pass next_after back as `after` to page on."""
        known(session_id)
        events = store.events(session_id, after=max(after, 0), limit=min(max(limit, 1), 200))
        return {"events": events, "next_after": events[-1]["seq"] if events else after}

    @mcp.tool(annotations=READ_ONLY)
    def recent_activity(since_minutes: int = 1440, limit: int = 100) -> dict[str, Any]:
        """Everything that happened across all sessions in the last N minutes."""
        since = manager.clock() - max(since_minutes, 1) * 60
        return {"events": store.recent_events(since, limit=min(max(limit, 1), 500))}

    @mcp.tool(annotations=READ_ONLY)
    def fleet_recap(since_minutes: int = 1440) -> dict[str, Any]:
        """One recap per session active in the last N minutes: 'what have my agents done?'"""
        return manager.fleet_recap(max(since_minutes, 1))

    @mcp.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False))
    async def cancel(session_id: str) -> dict[str, Any]:
        """Stop what Claude is doing in a session. The session can be used again afterwards."""
        known(session_id)
        return await manager.cancel(session_id)

    @mcp.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False))
    def close_session(session_id: str) -> dict[str, Any]:
        """Retire a session. Its history is kept."""
        known(session_id)
        try:
            return manager.close(session_id)
        except SessionBusy as exc:
            raise ToolError(str(exc)) from None

    def under_root(cwd: str | None) -> str | None:
        """Project name relative to the root, or None if the folder is outside it."""
        if not cwd:
            return None
        try:
            return str(manager.resolve_project(cwd).relative_to(manager.root))
        except ValueError:
            return None

    def live_sessions() -> list[dict[str, Any]]:
        out = []
        for f in sorted(live_dir.glob("*.json")) if live_dir.is_dir() else []:
            try:
                d = json.loads(f.read_text())
                os.kill(int(d["pid"]), 0)
            except (ValueError, KeyError, TypeError, OSError):
                continue  # unreadable, or the process is gone
            out.append(d)
        return out

    @mcp.tool(annotations=READ_ONLY)
    def list_active_sessions() -> dict[str, Any]:
        """Claude Code sessions running on this machine right now (e.g. open in a terminal),
        with whether each is busy or waiting for input."""
        return {"sessions": running()}

    def running() -> list[dict[str, Any]]:
        now_ms = manager.clock() * 1000
        sessions = []
        for d in live_sessions():
            name = under_root(d.get("cwd"))
            if name is None:
                continue
            sessions.append(
                {
                    "claude_session_id": d.get("sessionId"),
                    "name": d.get("name"),
                    "project": name,
                    "status": d.get("status"),
                    "minutes_since_update": round((now_ms - d.get("updatedAt", now_ms)) / 60000),
                }
            )
        sessions.sort(key=lambda s: s["minutes_since_update"])
        return sessions

    @mcp.tool(annotations=READ_ONLY)
    def list_claude_conversations(project: str | None = None, limit: int = 10) -> dict[str, Any]:
        """Recent Claude Code conversations on this machine, newest first, including ones
        started in a terminal. Optionally only for one project."""
        directory = None
        if project:
            try:
                directory = str(manager.resolve_project(project))
            except ValueError as exc:
                raise ToolError(str(exc)) from None
        limit = min(max(limit, 1), 50)
        now_ms = manager.clock() * 1000
        open_ids = {d.get("sessionId") for d in live_sessions()}
        listed = []
        for c in conversations(directory=directory, limit=limit * 5):
            name = under_root(c.cwd)
            if name is None:
                continue
            listed.append(
                {
                    "claude_session_id": c.session_id,
                    "project": name,
                    "summary": c.custom_title or c.summary,
                    "first_prompt": c.first_prompt,
                    "git_branch": c.git_branch,
                    "minutes_ago": round((now_ms - c.last_modified) / 60000),
                    "open_in_terminal": c.session_id in open_ids,
                }
            )
            if len(listed) == limit:
                break
        return {"conversations": listed}

    @mcp.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False))
    def attach_conversation(
        claude_session_id: str, project: str | None = None, label: str | None = None
    ) -> dict[str, Any]:
        """Continue an existing Claude Code conversation (from list_claude_conversations).
        If it is still open in a terminal, both copies will carry on separately."""
        if project is None:
            live = next(
                (d for d in live_sessions() if d.get("sessionId") == claude_session_id), None
            )
            match = None
            if live is None:
                match = next(
                    (c for c in conversations(limit=1000) if c.session_id == claude_session_id),
                    None,
                )
                if match is None:
                    raise ToolError(f"No Claude Code conversation with id {claude_session_id}")
            project = (live or {}).get("cwd") or (match.cwd if match else "") or ""
            label = (
                label
                or (live or {}).get("name")
                or (match and (match.custom_title or match.summary))
            )
        try:
            path = str(manager.resolve_project(project))
        except ValueError as exc:
            raise ToolError(str(exc)) from None
        try:
            history = transcripts.recent_turns(read_transcript(claude_session_id, path), limit=10)
        except Exception:
            history = []
        return manager.attach(claude_session_id, path, label, history=history)

    def find_live(session: str) -> dict[str, Any]:
        for d in live_sessions():
            if session in (d.get("name"), d.get("sessionId")) and under_root(d.get("cwd")):
                return d
        raise ToolError(f"No running session called {session!r}; see list_active_sessions")

    @mcp.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=True))
    async def message_active_session(session: str, message: str) -> dict[str, Any]:
        """Send a message into a Claude Code session that is open right now (e.g. in a
        terminal), by its name or id from list_active_sessions. It arrives in that very
        window. Read its answer later with read_session_output."""
        target = find_live(session)
        out = await deliver(target["name"], message)
        if not out.get("delivered"):
            raise ToolError(f"Could not deliver: {out.get('detail') or 'unknown reason'}")
        return {"session": target["name"], **out}

    @mcp.tool(annotations=READ_ONLY)
    def read_session_output(session: str, limit: int = 6) -> dict[str, Any]:
        """The latest prompts and replies of a running Claude Code session (name or id from
        list_active_sessions), read from its own transcript."""
        target = find_live(session)
        msgs = read_transcript(target["sessionId"], target.get("cwd"))
        return {
            "session": target["name"],
            "status": target.get("status"),
            "turns": transcripts.recent_turns(msgs, limit=min(max(limit, 1), 30)),
        }

    @mcp.tool(annotations=READ_ONLY)
    def search_session_history(session: str, query: str, limit: int = 5) -> dict[str, Any]:
        """Search the whole conversation of a running Claude Code session (name or id from
        list_active_sessions) for keywords; returns only the most relevant excerpts.
        Use instead of reading long output when looking for something said earlier."""
        target = find_live(session)
        turns = transcripts.recent_turns(
            read_transcript(target["sessionId"], target.get("cwd")), None
        )
        matches = transcripts.search_turns(turns, query, limit=min(max(limit, 1), 20))
        return {"session": target["name"], "total_turns": len(turns), "matches": matches}

    @mcp.tool(annotations=READ_ONLY)
    def list_pending_approvals(session_id: str | None = None) -> dict[str, Any]:
        """Tool calls Claude is waiting to be allowed to make. Read each one to the user."""
        if manager.approvals is None:
            return {"approvals": []}
        return {"approvals": manager.approvals.pending(session_id)}

    @mcp.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=True))
    def approve(approval_id: str) -> dict[str, Any]:
        """Allow one pending tool call. Only after the user has clearly said yes to it."""
        return _answer(lambda b: b.approve(approval_id))

    @mcp.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False))
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
    logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s")
    uvicorn.run(app, host=cfg.host, port=cfg.port, log_level="info")
