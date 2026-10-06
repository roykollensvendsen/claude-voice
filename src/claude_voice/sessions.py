"""Start, follow, resume and stop Claude Code conversations through the Agent SDK.

One bridge session maps to one Claude conversation. Each prompt is a turn run by
a fresh SDK client that resumes the conversation by its Claude session id, so a
turn survives nothing but the conversation does: it lives on disk in Claude
Code's own session store and outlasts restarts of this bridge.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ResultMessage,
    SystemMessage,
    TextBlock,
    ToolUseBlock,
)

from .store import Store

if TYPE_CHECKING:
    from .approvals import ApprovalBroker

CANCEL_GRACE_SECONDS = 10.0
MAX_FIELD_CHARS = 300

ClientFactory = Callable[[ClaudeAgentOptions], Any]


class SessionBusy(RuntimeError):
    pass


class SessionClosed(RuntimeError):
    pass


class SessionManager:
    def __init__(
        self,
        store: Store,
        project_root: str | Path,
        client_factory: ClientFactory = ClaudeSDKClient,
        clock: Callable[[], float] = time.time,
        approvals: ApprovalBroker | None = None,
    ) -> None:
        self.store = store
        self.root = Path(project_root).expanduser().resolve()
        self.client_factory = client_factory
        self.clock = clock
        self.approvals = approvals
        self._tasks: dict[str, asyncio.Task] = {}
        self._clients: dict[str, Any] = {}
        self._cancelling: set[str] = set()

    # -- lifecycle -----------------------------------------------------------

    def resolve_project(self, project: str) -> Path:
        path = Path(project).expanduser()
        if not path.is_absolute():
            path = self.root / path
        path = path.resolve()
        # RULE: a project outside the root is refused
        if path != self.root and self.root not in path.parents:
            raise ValueError(f"project must be under {self.root}")
        if not path.is_dir():
            raise ValueError(f"not a directory: {path}")
        return path

    def create(self, project: str, label: str | None = None) -> dict[str, Any]:
        path = self.resolve_project(project)
        return self.store.create_session(str(path), label)

    def attach(
        self,
        claude_session_id: str,
        project: str,
        label: str | None = None,
        history: list[dict[str, Any]] | None = None,
    ) -> dict:
        """Adopt a Claude conversation started elsewhere, e.g. in a terminal.

        `history` (recent turns from its transcript) is journalled so recaps and
        the event log show what happened before the bridge got involved.
        """
        s = self.create(project, label)
        self.store.update_session(s["id"], claude_session_id=claude_session_id)
        self.store.add_event(s["id"], "attached", {"claude_session_id": claude_session_id})
        for turn in history or []:
            self.store.add_event(s["id"], "history", turn)
        return self.store.get_session(s["id"])

    def close(self, session_id: str) -> dict[str, Any]:
        if self.is_running(session_id):
            raise SessionBusy("cancel the running turn before closing")
        self.store.get_session(session_id)
        self.store.update_session(session_id, status="closed")
        self.store.add_event(session_id, "closed", {})
        return self.store.get_session(session_id)

    def is_running(self, session_id: str) -> bool:
        task = self._tasks.get(session_id)
        return task is not None and not task.done()

    # -- turns ---------------------------------------------------------------

    async def send(self, session_id: str, prompt: str) -> dict[str, Any]:
        s = self.store.get_session(session_id)
        if s["status"] == "closed":
            raise SessionClosed(session_id)
        if self.is_running(session_id):
            raise SessionBusy("Claude is still working on the previous prompt")
        if not prompt.strip():
            raise ValueError("prompt is empty")

        self.store.update_session(session_id, status="running", last_error=None)
        self.store.add_event(session_id, "prompt", {"text": prompt})
        self._tasks[session_id] = asyncio.create_task(self._run(session_id, prompt), name=f"claude-turn:{session_id}")
        return {"session_id": session_id, "status": "running"}

    async def wait(self, session_id: str) -> None:
        task = self._tasks.get(session_id)
        if task is not None:
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def cancel(self, session_id: str) -> dict[str, Any]:
        self.store.get_session(session_id)
        task = self._tasks.get(session_id)
        if task is None or task.done():
            return {"session_id": session_id, "cancelled": False, "reason": "nothing running"}

        self._cancelling.add(session_id)
        if self.approvals is not None:
            self.approvals.withdraw(session_id)
        client = self._clients.get(session_id)
        if client is not None:
            with contextlib.suppress(Exception):
                await client.interrupt()
        try:
            await asyncio.wait_for(asyncio.shield(task), CANCEL_GRACE_SECONDS)
        except (TimeoutError, asyncio.CancelledError):
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        return {"session_id": session_id, "cancelled": True}

    def _options(self, session_id: str, s: dict[str, Any]) -> ClaudeAgentOptions:
        opts = ClaudeAgentOptions(
            cwd=s["project_path"],
            resume=s["claude_session_id"],
            # Behave like the user's own Claude Code: their CLAUDE.md, settings
            # and permission rules apply, and nothing is silently pre-approved.
            setting_sources=["user", "project", "local"],
            permission_mode="default",
            # The owner's claude.ai connectors (Calendar, Drive, ...) are not the
            # task's business, and their sign-in notes would be read aloud.
            settings=json.dumps({"disableClaudeAiConnectors": True}),
        )
        if self.approvals is not None:
            opts.can_use_tool = self.approvals.callback(session_id)
        return opts

    async def _run(self, session_id: str, prompt: str) -> None:
        s = self.store.get_session(session_id)
        final_status, error = "idle", None
        try:
            async with self.client_factory(self._options(session_id, s)) as client:
                self._clients[session_id] = client
                await client.query(prompt)
                async for msg in client.receive_response():
                    outcome = self._record(session_id, msg)
                    if outcome is not None:
                        final_status, error = outcome
        except asyncio.CancelledError:
            final_status = "cancelled"
            raise
        except Exception as exc:
            final_status, error = "error", f"{type(exc).__name__}: {exc}"
            self.store.add_event(session_id, "error", {"error": error})
        finally:
            self._clients.pop(session_id, None)
            if session_id in self._cancelling:
                self._cancelling.discard(session_id)
                final_status, error = "cancelled", None
                self.store.add_event(session_id, "cancelled", {})
            self.store.update_session(session_id, status=final_status, last_error=error)

    def _record(self, session_id: str, msg: Any) -> tuple[str, str | None] | None:
        """Journal one SDK message; return (status, error) when it ends the turn."""
        sid = session_id
        if isinstance(msg, SystemMessage):
            claude_id = msg.data.get("session_id")
            if msg.subtype == "init" and claude_id:
                self.store.update_session(sid, claude_session_id=claude_id)
        elif isinstance(msg, AssistantMessage):
            for block in msg.content:
                if isinstance(block, TextBlock) and block.text.strip():
                    self.store.add_event(sid, "text", {"text": block.text})
                elif isinstance(block, ToolUseBlock):
                    self.store.add_event(sid, "tool_use", {"name": block.name, "input": brief(block.input)})
        elif isinstance(msg, ResultMessage):
            if msg.session_id:
                self.store.update_session(sid, claude_session_id=msg.session_id)
            self.store.add_event(
                sid,
                "result",
                {
                    "text": msg.result,
                    "is_error": msg.is_error,
                    "num_turns": msg.num_turns,
                    "duration_ms": msg.duration_ms,
                },
            )
            if msg.is_error:
                return "error", msg.result or msg.subtype
            if msg.num_turns == 0 and not (msg.result or "").strip():
                # Seen when resuming a conversation that is open in a terminal.
                return "error", (
                    "Claude did nothing; the conversation may be open in another "
                    "Claude Code window. Use message_active_session to reach it there."
                )
            return "idle", None
        return None

    # -- recaps --------------------------------------------------------------

    def recap(self, session_id: str) -> dict[str, Any]:
        """A deterministic, voice-sized summary of where a session stands."""
        s = self.store.get_session(session_id)
        events = self.store.tail(session_id, 200)
        last_prompt_at = max((i for i, e in enumerate(events) if e["kind"] == "prompt"), default=None)
        turn = events[last_prompt_at:] if last_prompt_at is not None else []

        def last(kind: str, key: str = "text") -> Any:
            return next((e["payload"].get(key) for e in reversed(turn) if e["kind"] == kind), None)

        def last_history(role: str) -> Any:
            return next(
                (
                    e["payload"]["text"]
                    for e in reversed(events)
                    if e["kind"] == "history" and e["payload"].get("role") == role
                ),
                None,
            )

        return {
            "session_id": s["id"],
            "label": s["label"],
            "project_path": s["project_path"],
            "status": s["status"],
            "last_prompt": turn[0]["payload"]["text"] if turn else last_history("user"),
            "latest_text": last("text") if turn else last_history("assistant"),
            "last_result": last("result"),
            "tools_used": dict(Counter(e["payload"]["name"] for e in turn if e["kind"] == "tool_use")),
            "last_error": s["last_error"],
            "pending_approvals": self.approvals.pending(session_id) if self.approvals else [],
            "updated_at": s["updated_at"],
        }

    def fleet_recap(self, since_minutes: int = 1440, limit: int = 20) -> dict[str, Any]:
        cutoff = self.clock() - since_minutes * 60
        sessions = [s for s in self.store.list_sessions(limit=limit) if s["updated_at"] >= cutoff]
        return {
            "since_minutes": since_minutes,
            "sessions": [self.recap(s["id"]) for s in sessions],
        }


def brief(value: Any) -> Any:
    """Shrink tool inputs (a whole file for Write, say) to something worth reading aloud."""
    if isinstance(value, dict):
        return {k: brief(v) for k, v in value.items()}
    if isinstance(value, list):
        return [brief(v) for v in value[:20]]
    if isinstance(value, str) and len(value) > MAX_FIELD_CHARS:
        return value[:MAX_FIELD_CHARS] + f"… ({len(value)} chars)"
    return value
