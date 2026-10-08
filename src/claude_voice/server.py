"""The MCP face of the bridge: tools a voice assistant can call, over stdio or HTTP."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from claude_agent_sdk import SDKSessionInfo
from claude_agent_sdk import list_sessions as list_claude_sessions
from mcp.server import MCPServer
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import AnyHttpUrl
from starlette.types import ASGIApp

from . import events, metrics, transcripts
from .approvals import ApprovalBroker, ApprovalNotFound
from .metrics import METRICS
from .oauth import SCOPE, OAuthProvider, PublicClientMetadata
from .sessions import SessionBusy, SessionClosed, SessionManager
from .store import SessionNotFound, Store
from .tree import PROJECTS_DIR, SessionTree

MIN_TOKEN_CHARS = 32
READ_ONLY = ToolAnnotations(read_only_hint=True)
COURIER_PREFIX = "claude-voice-msg-"
# Claude Code keeps one JSON file per running session here.
LIVE_DIR = Path("~/.claude/sessions").expanduser()

INSTRUCTIONS = """\
Controls Claude Code sessions running on the user's own machine. The user is
usually speaking, often while driving, so keep what you read back short.
Poll whats_new (pass back its cursor) for news; it is cheap.
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
    courier_name: str = transcripts.COURIER_NAME


def load_config(env: Mapping[str, str], transport: str) -> Config:
    # RULE: an api key in the environment stops the bridge from starting
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
    cfg.courier_name = env.get("CLAUDE_VOICE_COURIER_NAME", "").strip() or transcripts.COURIER_NAME
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


def _parse_cursor(cursor: str | None) -> tuple[int | None, int | None]:
    try:
        b, f = (cursor or "").split(".")
        return int(b.removeprefix("b")), int(f.removeprefix("f"))
    except ValueError:
        return None, None


async def log_requests(ctx, call_next):
    """Log and count each MCP call: method, tool, outcome, time and the caller's trace id.

    A voice client sends a trace id per spoken turn as params._meta.trace_id; it is
    kept for the courier too, so one search follows a turn through every part.
    """
    params = ctx.params if isinstance(ctx.params, dict) else {}
    tool = str(params.get("name", ""))
    what = f"{ctx.method} {tool}".rstrip()
    meta = params.get("_meta") if isinstance(params.get("_meta"), dict) else {}
    trace = str(meta.get("trace_id") or "") or None
    token = transcripts.current_trace.set(trace)
    started = time.monotonic()
    try:
        result = await call_next(ctx)
    except Exception as exc:
        ms = (time.monotonic() - started) * 1000
        log.info("mcp %s -> %s %dms trace=%s", what, type(exc).__name__, ms, trace or "-")
        if ctx.method == "tools/call":
            METRICS.record(tool, ms, error=f"{type(exc).__name__}: {exc}", kind="exception", trace_id=trace)
        raise
    finally:
        transcripts.current_trace.reset(token)
    ms = (time.monotonic() - started) * 1000
    if ctx.method == "tools/call":
        failed = getattr(result, "is_error", None) or (isinstance(result, dict) and result.get("isError"))
        log.info("mcp %s -> %s %dms trace=%s", what, "error" if failed else "ok", ms, trace or "-")
        error = None
        if failed:
            content = getattr(result, "content", None) or (result.get("content") if isinstance(result, dict) else None)
            first = content[0] if content else None
            error = str(getattr(first, "text", None) or (first.get("text") if isinstance(first, dict) else first) or "")
        METRICS.record(tool, ms, error=error, trace_id=trace)
    elif ctx.request_id is not None:
        log.info("mcp %s", what)
    return result


def build_server(
    manager: SessionManager,
    oauth: OAuthProvider | None = None,
    conversations: Callable[..., list[SDKSessionInfo]] = list_claude_sessions,
    live_dir: Path = LIVE_DIR,
    deliver: Callable[[str, str], Awaitable[dict[str, Any]]] = transcripts.deliver,
    read_transcript: Callable[[str, str | None], list[Any]] | None = None,
    watch_interval: float | None = None,
    restart: dict[str, Any] | None = None,
    poll_seconds: float = 1.0,
    receive_seconds: float = 15.0,
    projects_dir: Path = PROJECTS_DIR,
) -> MCPServer:
    auth = None
    if oauth is not None:
        auth = AuthSettings(
            issuer_url=AnyHttpUrl(oauth.public_url),
            resource_server_url=AnyHttpUrl(f"{oauth.public_url}/mcp"),
            client_registration_options=ClientRegistrationOptions(
                enabled=True, valid_scopes=[SCOPE], default_scopes=[SCOPE]
            ),
            required_scopes=[SCOPE],
            revocation_options=RevocationOptions(enabled=True),
            validate_token_resource=False,
        )
    if read_transcript is None:

        def read_transcript(sid: str, directory: str | None) -> list[Any]:
            return transcripts.read_raw_transcript(sid, directory or "", projects_dir)

    watcher = events.LiveWatcher(manager.store, live_dir, manager.root, clock=manager.clock)
    tree = SessionTree(manager.store, live_dir, manager.root, projects_dir=projects_dir, clock=manager.clock)
    tree_version: list[str] = []  # the last shape seen, so a change can be reported
    last_check: list[str] = []
    started_at = datetime.now(UTC).isoformat(timespec="seconds")
    restart = restart or {"at": started_at, "reason": None, "total": None}
    running_version = metrics.version()

    def check() -> None:
        """Look at the running sessions and the tree's shape; record what changed."""
        watcher.check()
        version = tree.build()["version"]
        if tree_version and tree_version[0] != version:
            watcher.record("claude-voice", "tree_changed", version)
        tree_version[:] = [version]
        last_check[:] = [datetime.now(UTC).isoformat(timespec="seconds")]

    @contextlib.asynccontextmanager
    async def lifespan(_server):
        # Watch running sessions between polls, so short-lived states are not missed.
        task = None
        if watch_interval:

            async def watch():
                while True:
                    with contextlib.suppress(Exception):
                        check()
                    await asyncio.sleep(watch_interval)

            task = asyncio.create_task(watch())
        try:
            yield {}
        finally:
            if task:
                task.cancel()

    mcp = MCPServer(
        "claude-voice",
        instructions=INSTRUCTIONS,
        auth_server_provider=oauth,
        auth=auth,
        middleware=[log_requests],
        lifespan=lifespan,
    )
    if oauth is not None:
        mcp.custom_route("/oauth/consent", methods=["GET"])(oauth.consent_page)
        mcp.custom_route("/oauth/consent", methods=["POST"])(oauth.consent_submit)
    store = manager.store

    def known(session_id: str) -> str:
        """Resolve a bridge session id, or a Claude session id the bridge has adopted,
        to the bridge's id. Explain when it is a terminal session the bridge never saw."""
        try:
            return store.get_session(session_id)["id"]
        except SessionNotFound:
            pass
        adopted = [s for s in store.list_sessions(limit=1000) if s["claude_session_id"] == session_id]
        if adopted:
            open_ = [s for s in adopted if s["status"] != "closed"]
            return (open_ or adopted)[0]["id"]
        if any(session_id in (d.get("sessionId"), d.get("name")) for d in live_sessions()):
            raise ToolError(
                f"{session_id} is a Claude Code session running on this machine (in a "
                "terminal or in the background), not one this bridge started or attached. "
                "Use message_active_session to talk to it, or "
                "attach_conversation to work on a copy of its conversation."
            )
        raise ToolError(f"No session with id {session_id}")

    @mcp.tool(annotations=READ_ONLY)
    def list_projects() -> dict[str, Any]:
        """List the project directories Claude can be started in."""
        names = sorted(p.name for p in manager.root.iterdir() if p.is_dir() and not p.name.startswith("."))
        return {"root": str(manager.root), "projects": names}

    @mcp.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False))
    def create_session(project: str, label: str | None = None) -> dict[str, Any]:
        """Start a new Claude Code session in a project (a name from list_projects)."""
        try:
            return manager.create(project, label)
        except ValueError as exc:
            raise ToolError(str(exc)) from None

    @mcp.tool(annotations=READ_ONLY)
    def list_sessions(status: str | None = None, project: str | None = None, limit: int = 20) -> dict[str, Any]:
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
        session_id = known(session_id)
        try:
            return await manager.send(session_id, prompt)
        except (SessionBusy, SessionClosed, ValueError) as exc:
            msg = str(exc) if not isinstance(exc, SessionClosed) else "That session is closed."
            raise ToolError(msg) from None

    @mcp.tool(annotations=READ_ONLY)
    def session_recap(session_id: str, max_chars: int = 400) -> dict[str, Any]:
        """Short status of one session: running or not, last prompt, latest words, result.
        Texts are cut to max_chars each."""
        session_id = known(session_id)
        recap = manager.recap(session_id)
        for key in ("last_prompt", "latest_text", "last_result", "last_error"):
            if isinstance(recap.get(key), str):
                recap[key] = transcripts.clip(recap[key], max(max_chars, 20))
        return recap

    @mcp.tool(annotations=READ_ONLY)
    def get_messages(session_id: str, after: int = 0, limit: int = 50) -> dict[str, Any]:
        """Detailed event log of a session. Pass next_after back as `after` to page on."""
        session_id = known(session_id)
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
        session_id = known(session_id)
        return await manager.cancel(session_id)

    @mcp.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False))
    def close_session(session_id: str) -> dict[str, Any]:
        """Retire a session. Its history is kept."""
        session_id = known(session_id)
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
        return events.read_live(live_dir)

    @mcp.tool(annotations=READ_ONLY)
    def list_active_sessions() -> dict[str, Any]:
        """Claude Code sessions running on this machine right now, with whether each is
        busy or waiting for input. kind: "interactive" is a terminal window the user
        types in; "bg" is a background or remote-controlled session."""
        return {"sessions": running()}

    def moved_to(d: dict[str, Any]) -> dict[str, Any] | None:
        """Where a running session's conversation went, if Claude Code continued it elsewhere."""
        new = transcripts.continued_in(str(d.get("sessionId")), str(d.get("cwd")), projects_dir)
        if not new:
            return None
        name = next((x.get("name") for x in live_sessions() if x.get("sessionId") == new), None)
        return {"id": new, "name": name}

    def running() -> list[dict[str, Any]]:
        managed = {
            s["claude_session_id"]
            for s in store.list_sessions(limit=1000)
            if s["claude_session_id"] and s["status"] != "closed"
        }
        now_ms = manager.clock() * 1000
        sessions = []
        for d in live_sessions():
            name = under_root(d.get("cwd"))
            if name is None:
                continue
            row = {
                "claude_session_id": d.get("sessionId"),
                "name": d.get("name"),
                "project": name,
                "status": d.get("status"),
                "kind": d.get("kind"),
                "managed_by_bridge": d.get("sessionId") in managed,
                "minutes_since_update": round((now_ms - d.get("updatedAt", now_ms)) / 60000),
            }
            moved = moved_to(d)
            if moved:
                row["status"], row["moved_to"] = "moved", moved
            elif d.get("status") == "waiting":
                row["waiting_for"] = d.get("waitingFor")
                question = transcripts.open_question(str(d.get("sessionId")), str(d.get("cwd")), projects_dir)
                if question:
                    row["question"] = question
            sessions.append(row)
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
            live = next((d for d in live_sessions() if d.get("sessionId") == claude_session_id), None)
            match = None
            if live is None:
                match = next(
                    (c for c in conversations(limit=1000) if c.session_id == claude_session_id),
                    None,
                )
                if match is None:
                    raise ToolError(f"No Claude Code conversation with id {claude_session_id}")
            project = (live or {}).get("cwd") or (match.cwd if match else "") or ""
            label = label or (live or {}).get("name") or (match.custom_title or match.summary if match else None)
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
            if str(d.get("name", "")).startswith(COURIER_PREFIX) or Path(str(d.get("cwd"))).name.startswith(
                COURIER_PREFIX
            ):
                continue  # the bridge's own messengers, gone as soon as they deliver
            if session in (d.get("name"), d.get("sessionId")) and under_root(d.get("cwd")):
                return d
        raise ToolError(f"No running session called {session!r}; see list_active_sessions")

    @mcp.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=True))
    async def message_active_session(session: str, message: str) -> dict[str, Any]:
        """Send a message into a Claude Code session that is open right now (e.g. in a
        terminal), by its name or id from list_active_sessions. It arrives in that very
        window. Read its answer later with read_session_output."""
        target = find_live(session)
        moved = moved_to(target)
        if moved:
            return {"session": target["name"], "delivered": False, "status": "moved", "moved_to": moved}
        out = await deliver(target["name"], message)
        if not out.get("delivered"):
            raise ToolError(f"Could not deliver: {out.get('detail') or 'unknown reason'}")
        return {"session": target["name"], "status": "delivered", **out}

    def numbered_turns(target: dict[str, Any]) -> list[dict[str, Any]]:
        msgs = read_transcript(target["sessionId"], target.get("cwd"))
        return [{"index": i, **turn} for i, turn in enumerate(transcripts.recent_turns(msgs, None))]

    @mcp.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=True))
    async def ask_active_session(
        session: str, message: str, wait_seconds: float = 60, max_chars: int = 600
    ) -> dict[str, Any]:
        """Ask a Claude Code session that is open right now, and wait for its answer.
        status: "answered", "needs_input" (it waits for its user), "still_working" (the
        wait ran out; continue with read_session_output(after=next_after)),
        "session_ended" (it exited meanwhile), "not_received" (the message never reached
        its conversation), "moved" (the conversation now lives in moved_to; nothing sent),
        or "needs_choice" (it waits for its user to pick one of question.options; answer
        at the screen) - "needs_input" before sending means nothing was sent."""
        target = find_live(session)
        moved = moved_to(target)
        if moved:
            return {
                "session": target["name"],
                "status": "moved",
                "moved_to": moved,
                "session_ended": False,
                "reply": "",
                "turns": [],
                "next_after": -1,
            }

        def waiting(d: dict[str, Any]) -> dict[str, Any] | None:
            """Why a session can take nothing in right now: an open question, or other input."""
            if d.get("status") != "waiting":
                return None
            question = transcripts.open_question(str(d.get("sessionId")), str(d.get("cwd")), projects_dir)
            return {"status": "needs_choice", "question": question} if question else {"status": "needs_input"}

        blocked = waiting(target)
        if blocked:
            # A message would only queue behind what it waits for; send nothing.
            return {
                "session": target["name"],
                **blocked,
                "session_ended": False,
                "reply": "",
                "turns": [],
                "next_after": -1,
            }
        before = len(numbered_turns(target))
        was_busy = target.get("status") in ("busy", "shell")
        out = await deliver(target["name"], message)
        if not out.get("delivered"):
            raise ToolError(f"Could not deliver: {out.get('detail') or 'unknown reason'}")

        pid, status, seen_busy = target.get("pid"), "still_working", False
        started = asyncio.get_running_loop().time()
        deadline = started + max(min(wait_seconds, 300), 0)
        probe = message.strip()[:80]
        while True:
            now = next((d for d in live_sessions() if d.get("pid") == pid), None)
            if now is None:
                status = "session_ended"
                break
            target = now
            # A quick turn can start and end between two looks, so a new reply
            # counts as much as having seen the session busy.
            seen_busy = seen_busy or now.get("status") in ("busy", "shell")
            new_turns = numbered_turns(now)[before:]
            anchor_at = next((t["index"] for t in new_turns if t["role"] == "user" and probe in t["text"]), None)
            if (
                was_busy
                and anchor_at is not None
                and any(t["role"] == "assistant" and t["index"] > anchor_at for t in new_turns)
            ):
                # Asked mid-work: its first words after our question are the answer; the
                # rest of its turn belongs to whatever it was doing.
                status = "answered"
                break
            replied = seen_busy or any(t["role"] == "assistant" for t in new_turns)
            arrived = replied or any(t["role"] == "user" and probe in t["text"] for t in new_turns)
            if not arrived and asyncio.get_running_loop().time() - started >= receive_seconds:
                status = "not_received"  # handed over, yet never reached the conversation
                break
            if replied and now.get("status") == "idle":
                status = "answered"
                break
            if now.get("status") == "waiting" and (seen_busy or replied or arrived):
                blocked = waiting(now)
                status = blocked["status"] if blocked else "needs_input"
                break
            if asyncio.get_running_loop().time() >= deadline:
                break
            await asyncio.sleep(poll_seconds)

        fresh = numbered_turns(target)[before:] if status != "session_ended" else []
        # Only what answers our message counts: what the session wrote after it. Its
        # last words are the reply; the rest of its turn is how it got there.
        anchor = next((t for t in fresh if t["role"] == "user" and probe in t["text"]), None)
        start = anchor["index"] if anchor else before - 1
        new = [t for t in fresh if t["role"] == "assistant" and t["index"] > start]
        if anchor is None and status != "still_working":
            new = new[-1:]  # the message is not in view: only the session's final words
        elif anchor is not None and was_busy and new:
            new = new[:1]
        for turn in new:
            turn["text"] = transcripts.clip(turn["text"], max(max_chars, 20))
        return {
            "session": target["name"],
            "status": status,
            "session_ended": status == "session_ended",
            "reply": new[-1]["text"] if new else "",
            "turns": new,
            "next_after": new[-1]["index"] if new else start,
            **({"question": blocked["question"]} if status == "needs_choice" and blocked else {}),
        }

    @mcp.tool(annotations=READ_ONLY)
    def read_session_output(
        session: str,
        limit: int = 6,
        max_chars: int = 600,
        after: int | None = None,
        turn: int | None = None,
        offset: int = 0,
    ) -> dict[str, Any]:
        """The latest turns of a running Claude Code session (name or id from
        list_active_sessions). Turns are numbered: pass next_after back as `after` to get
        only new ones. A turn longer than max_chars is cut and says so (cut, length); read
        it whole with `turn` (its index) and `offset`, page by page via next_offset."""
        target = find_live(session)
        turns = numbered_turns(target)
        size = max(max_chars, 20)
        if turn is not None:
            whole = next((t for t in turns if t["index"] == turn), None)
            if whole is None:
                raise ToolError(f"No turn {turn} in {target['name']}")
            start = min(max(offset, 0), len(whole["text"]))
            chunk = whole["text"][start : start + size]
            end = start + len(chunk)
            return {
                "session": target["name"],
                "status": target.get("status"),
                "turn": turn,
                "role": whole["role"],
                "text": chunk,
                "offset": start,
                "next_offset": end,
                "has_more": end < len(whole["text"]),
                "length": len(whole["text"]),
            }
        if after is None:
            turns = turns[-min(max(limit, 1), 30) :]
        else:
            turns = [t for t in turns if t["index"] > after][: min(max(limit, 1), 30)]
        for t in turns:
            t["length"] = len(t["text"])
            t["cut"] = t["length"] > size
            t["text"] = transcripts.clip(t["text"], size)
        next_after = turns[-1]["index"] if turns else (after if after is not None else -1)
        return {"session": target["name"], "status": target.get("status"), "turns": turns, "next_after": next_after}

    @mcp.tool(annotations=READ_ONLY)
    def search_session_history(session: str, query: str, limit: int = 5, max_chars: int = 300) -> dict[str, Any]:
        """Search the whole conversation of a running Claude Code session (name or id from
        list_active_sessions) for keywords; returns only the most relevant excerpts.
        Use instead of reading long output when looking for something said earlier."""
        target = find_live(session)
        turns = transcripts.recent_turns(read_transcript(target["sessionId"], target.get("cwd")), None)
        size = max(max_chars, 40)
        matches = transcripts.search_turns(turns, query, limit=min(max(limit, 1), 20), context_chars=size // 2 - 10)
        for match in matches:
            match["text"] = transcripts.clip(match["text"], size)
        return {"session": target["name"], "total_turns": len(turns), "matches": matches}

    @mcp.tool(annotations=READ_ONLY)
    def whats_new(cursor: str | None = None) -> dict[str, Any]:
        """Has anything happened since I last asked? Returns only new events (a session
        finished, failed, needs approval or input, started, ended) and a cursor to pass
        next time. Call without a cursor first; cheap enough to poll."""
        check()
        b, f = _parse_cursor(cursor)
        latest_b, latest_f = events.last_bridge_seq(manager.store), watcher.last_seq()
        items: list[dict[str, Any]] = []
        if b is not None:
            items = events.bridge_events(manager.store, b) + watcher.feed_after(f or 0)
            items.sort(key=lambda e: e["ts"])
        nb, nf = max(b or 0, latest_b), max(f or 0, latest_f)
        return {
            "cursor": f"b{nb}.f{nf}",
            "events": [{k: v for k, v in e.items() if k not in ("seq",)} for e in items],
        }

    @mcp.tool(annotations=READ_ONLY)
    def session_tree() -> dict[str, Any]:
        """The sessions to choose from, as a tree: the bridge, the Claude Code sessions
        running on this machine, the sessions the bridge runs, helper agents, and for each
        which others it has sent messages to in the last day (talks_to). `version` changes
        when the shape changes; whats_new reports that as kind "tree_changed"."""
        return tree.build()

    @mcp.tool(annotations=READ_ONLY)
    def health() -> dict[str, Any]:
        """The bridge's own state: since when and which version, memory, the last restart,
        calls/errors/latency per tool since start, the latest errors, courier and watcher."""
        snap = METRICS.snapshot()
        courier = transcripts.shared_courier()
        return {
            "now": {
                "up_since": started_at,
                "version": running_version,
                "memory_rss_mb": metrics.memory_rss_mb(),
                "last_restart": {"at": restart["at"], "reason": restart["reason"]},
                "restarts_total": restart["total"],
            },
            "tools": snap["tools"],
            "errors": snap["errors"],
            "courier": {"warm": courier.warm, "last_delivery_ms": courier.last_delivery_ms},
            "watcher": {
                "last_check_at": last_check[0] if last_check else None,
                "tree_version": tree_version[0] if tree_version else tree.build()["version"],
            },
        }

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
        allowed_origins=[f"https://{h}" for h in public_hosts or []] + ["http://127.0.0.1:*", "http://localhost:*"],
    )
    return PublicClientMetadata(server.streamable_http_app(transport_security=security))


def serve_forever(cfg: Config) -> None:
    """Run the bridge with a checked configuration until stopped."""
    transcripts.configure_courier(cfg.courier_name)
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
    restart = metrics.restart_info("~/.local/state/claude-voice/restarts")
    app = build_app(build_server(manager, oauth=oauth, watch_interval=5.0, restart=restart), cfg.public_hosts)
    logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s")
    uvicorn.run(app, host=cfg.host, port=cfg.port, log_level="info")
