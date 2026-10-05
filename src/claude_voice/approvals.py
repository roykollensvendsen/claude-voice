"""Hold Claude's risky tool calls until the user says yes or no.

Claude Code asks `can_use_tool` only for calls its own permission rules (the
user's settings) do not already allow. Read-only tools are let through here;
anything else becomes a pending request that the voice assistant reads out and
answers with approve/deny. Silence is a no.
"""

from __future__ import annotations

import asyncio
import contextlib
import itertools
from typing import Any

from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny, ToolPermissionContext

from .sessions import brief
from .store import Store

READ_ONLY_TOOLS = frozenset({"Read", "Glob", "Grep", "LS", "NotebookRead", "TodoWrite"})
DEFAULT_TIMEOUT_SECONDS = 600.0


class ApprovalNotFound(KeyError):
    pass


class ApprovalBroker:
    def __init__(
        self,
        store: Store,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        auto_allow: frozenset[str] = READ_ONLY_TOOLS,
    ) -> None:
        self.store = store
        self.timeout = timeout_seconds
        self.auto_allow = auto_allow
        self._ids = itertools.count(1)
        self._pending: dict[str, tuple[dict[str, Any], asyncio.Future]] = {}

    def callback(self, session_id: str):
        async def can_use_tool(
            tool: str, tool_input: dict[str, Any], context: ToolPermissionContext
        ) -> PermissionResultAllow | PermissionResultDeny:
            if tool in self.auto_allow:
                return PermissionResultAllow()
            return await self._ask(session_id, tool, tool_input, context)

        return can_use_tool

    async def _ask(
        self, session_id: str, tool: str, tool_input: dict[str, Any], context: ToolPermissionContext
    ) -> PermissionResultAllow | PermissionResultDeny:
        req = {
            "id": str(next(self._ids)),
            "session_id": session_id,
            "tool": tool,
            "input": brief(tool_input),
            "reason": context.decision_reason or context.description or context.title,
        }
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[req["id"]] = (req, future)
        self.store.add_event(session_id, "approval_requested", req)
        try:
            return await asyncio.wait_for(asyncio.shield(future), self.timeout)
        except TimeoutError:
            self.store.add_event(session_id, "approval_expired", {"id": req["id"]})
            # RULE: an approval nobody answers is refused
            return PermissionResultDeny(message="Not approved in time; the user did not answer.")
        finally:
            self._pending.pop(req["id"], None)

    def pending(self, session_id: str | None = None) -> list[dict[str, Any]]:
        return [req for req, _ in self._pending.values() if session_id is None or req["session_id"] == session_id]

    def _settle(self, approval_id: str, outcome: PermissionResultAllow | PermissionResultDeny):
        entry = self._pending.pop(approval_id, None)
        if entry is None or entry[1].done():
            raise ApprovalNotFound(approval_id)
        req, future = entry
        future.set_result(outcome)
        return req

    def approve(self, approval_id: str) -> dict[str, Any]:
        req = self._settle(approval_id, PermissionResultAllow())
        self.store.add_event(req["session_id"], "approval_granted", {"id": approval_id})
        return req

    def deny(self, approval_id: str, reason: str | None = None) -> dict[str, Any]:
        message = reason or "The user said no."
        req = self._settle(approval_id, PermissionResultDeny(message=message))
        self.store.add_event(req["session_id"], "approval_denied", {"id": approval_id, "reason": message})
        return req

    def withdraw(self, session_id: str) -> None:
        """Refuse everything a session is waiting on, e.g. because it is being cancelled."""
        for req in self.pending(session_id):
            with contextlib.suppress(ApprovalNotFound):
                self._settle(req["id"], PermissionResultDeny(message="Cancelled.", interrupt=True))
