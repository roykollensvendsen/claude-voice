"""One fleet across machines: the hub's session tools see and reach every approved machine.

A session on another machine is named with its machine in front, ``lukas-laptop/mod``
(ADR-CV-006). The session tools take that name and pass the call to the machine, and
the tools that look across sessions add each machine's sessions to their own. All
machines are asked at once, each for two seconds at most; one that does not answer is
reported with its state, never waited for.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from . import machines

log = logging.getLogger("claude_voice")

ASK_SECONDS = 2.0
FRESH_SECONDS = 10.0  # how old an answer about a machine may be before it is asked again
SLOW_AFTER = 60.0  # a machine that answered this recently, and not now, is slow rather than offline

# The session tools that take a session, and the argument that names it.
ROUTED = {
    "get_active_session": "session",
    "read_session_output": "session",
    "message_active_session": "session",
    "ask_active_session": "session",
    "rename_session": "session",
    "stop_active_session": "name",
    "session_recap": "session_id",
    "send_task": "session_id",
    "cancel": "session_id",
    "close_session": "session_id",
}
LISTS = ("list_active_sessions", "fleet_recap")


def _now_iso(at: float) -> str:
    return datetime.fromtimestamp(at, UTC).isoformat(timespec="seconds")


class Fleet:
    """What the hub knows of each machine, asked again when it is older than ten seconds."""

    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self.machines: list[machines.Machine] = []
        self.clock = clock
        self._seen: dict[str, dict[str, Any]] = {}  # per machine: its state and sessions

    def _machine(self, name: str) -> machines.Machine | None:
        return next((m for m in self.machines if m.machine.name == name), None)

    def split(self, session: Any) -> tuple[machines.Machine, str] | None:
        """(machine, its own name for the session) when `session` names one elsewhere."""
        if not isinstance(session, str) or "/" not in session:
            return None
        name, _, rest = session.partition("/")
        machine = self._machine(name)
        return (machine, rest) if machine is not None and rest else None

    async def _ask(self, machine: machines.Machine) -> None:
        name, now = machine.machine.name, self.clock()
        before = self._seen.get(name, {})
        started = time.monotonic()
        try:
            # RULE: looking across machines never waits long for one
            answer = await asyncio.wait_for(machine.call("fleet_recap", {}), ASK_SECONDS)
            rows = list((answer.structured_content or {}).get("sessions") or [])
            seen = {"state": "online", "error": None, "last_seen": now, "sessions": rows}
            seen["latency_ms"] = round((time.monotonic() - started) * 1000)
        except (TimeoutError, RuntimeError) as exc:
            seen = {**self._trouble(machine, exc, before, now), "last_seen": before.get("last_seen"), "sessions": []}
            seen["latency_ms"] = None
        self._seen[name] = {**seen, "checked_at": now}
        if before.get("state") != seen["state"]:
            log.info("machine %s is %s", name, seen["state"])

    def _trouble(self, machine: machines.Machine, exc: Exception, before: dict[str, Any], now: float) -> dict[str, Any]:
        cause = machine.last_failure
        refused = machines._refusal(cause) if cause is not None else None
        if refused is not None:
            error = "other tools than approved" if "other tools" in str(refused) else "not the machine you approved"
            return {"state": "not_approved", "error": error}
        last = before.get("last_seen")
        recently = last is not None and now - float(last) < SLOW_AFTER
        refused_port = cause is not None and "refused" in str(cause).lower()
        state = "slow" if recently else "agent_down" if refused_port else "offline"
        return {"state": state, "error": "did not answer"}

    async def refresh(self) -> None:
        """Ask every machine whose answer is older than ten seconds, all at once."""
        due = [
            m
            for m in self.machines
            if self.clock() - self._seen.get(m.machine.name, {}).get("checked_at", 0) >= FRESH_SECONDS
        ]
        if due:
            await asyncio.gather(*(self._ask(m) for m in due))

    def rows(self) -> list[dict[str, Any]]:
        """Each machine as the voice and the page see it."""
        out = []
        for m in self.machines:
            seen = self._seen.get(m.machine.name, {})
            last = seen.get("last_seen")
            out.append(
                {
                    "name": m.machine.name,
                    "state": seen.get("state", "offline"),
                    "reachable": seen.get("state") in ("online",),
                    "error": seen.get("error"),
                    "seen_as": m.approval.get("seen_as"),
                    "latency_ms": seen.get("latency_ms"),
                    "checked_at": _now_iso(seen["checked_at"]) if seen.get("checked_at") else None,
                    "last_seen": _now_iso(last) if last else None,
                    "sessions": "list_active_sessions" in m.spec.tools,
                }
            )
        return out

    def sessions(self) -> list[dict[str, Any]]:
        """Every session on the other machines, named with the machine in front."""
        out = []
        for m in self.machines:
            for row in self._seen.get(m.machine.name, {}).get("sessions", []):
                out.append({**row, "name": f"{m.machine.name}/{row.get('name')}", "machine": m.machine.name})
        return out


def _result(body: dict[str, Any]) -> Any:
    from mcp.types import CallToolResult, TextContent

    return CallToolResult(content=[TextContent(type="text", text=json.dumps(body))], structured_content=body)


def _named_back(body: Any, machine: str, own: str) -> Any:
    """A machine's answer with the session's name as the hub knows it."""
    if not isinstance(body, dict):
        return body
    out = dict(body)
    for key in ("session", "name"):
        if out.get(key) == own:
            out[key] = f"{machine}/{own}"
    return out


def middleware(fleet: Fleet) -> Any:
    """Pass a call that names a session elsewhere to its machine, and add the other
    machines' sessions and states to the tools that look across sessions."""

    async def across(ctx: Any, call_next: Callable[[Any], Awaitable[Any]]) -> Any:
        params = ctx.params if isinstance(ctx.params, dict) else {}
        tool = str(params.get("name", ""))
        if ctx.method != "tools/call" or not fleet.machines:
            return await call_next(ctx)
        arguments = dict(params.get("arguments") or {})
        key = ROUTED.get(tool)
        target = fleet.split(arguments.get(key)) if key else None
        if target is not None and key is not None:
            machine, own = target
            # RULE: a session on another machine is reached by the machine name in front
            try:
                answer = await machine.call(tool, {**arguments, key: own})
            except RuntimeError as exc:
                from mcp.types import CallToolResult, TextContent

                return CallToolResult(content=[TextContent(type="text", text=str(exc))], is_error=True)
            if answer.is_error:
                return answer
            return _result(_named_back(answer.structured_content, machine.machine.name, own))
        result = await call_next(ctx)
        if tool in (*LISTS, "health") and not getattr(result, "is_error", False):
            await fleet.refresh()
            body = dict(getattr(result, "structured_content", None) or {})
            if tool in LISTS:
                body["sessions"] = [*body.get("sessions", []), *fleet.sessions()]
            body["machines"] = fleet.rows()
            with contextlib.suppress(Exception):
                return _result(body)
        return result

    return across
