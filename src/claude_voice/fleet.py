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
import re
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
    "set_active_session": "session",  # answered by the hub itself, which keeps the active session
}
LISTS = ("list_active_sessions", "fleet_recap")
# A session named by its short number, as said aloud: "3", "nr 3", "nr. 3", "nummer 3", "number 3".
SPOKEN_NUMBER = re.compile(r"(?:nr\.?|nummer|number)?\s*(\d{1,4})", re.IGNORECASE)


def _now_iso(at: float) -> str:
    return datetime.fromtimestamp(at, UTC).isoformat(timespec="seconds")


class Fleet:
    """What the hub knows of each machine, asked again when it is older than ten seconds."""

    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self.machines: list[machines.Machine] = []
        self.clock = clock
        self._seen: dict[str, dict[str, Any]] = {}  # per machine: its state and sessions
        self._asked_at: dict[str, str] = {}  # when the hub first saw each remote question
        self.publish: Callable[[str, dict[str, Any]], None] = lambda event, data: None
        self.numbers: Any = None  # the hub's Store, which hands out the short session numbers

    def _machine(self, name: str) -> machines.Machine | None:
        return next((m for m in self.machines if m.machine.name == name), None)

    def numbered(self) -> dict[str, int]:
        """The hub's short number for every session it knows on the other machines."""
        ids = [
            f"{m.machine.name}/{row.get('id') or row.get('claude_session_id')}"
            for m in self.machines
            for row in self._seen.get(m.machine.name, {}).get("sessions", [])
        ]
        if self.numbers is None:
            return {}
        # RULE: a session on another machine gets its number from the same pool as the hub
        return dict(self.numbers.number_sessions(ids, scope=lambda sid: "/" in sid))

    def _number_of(self, numbers: dict[str, int], machine: str, row: dict[str, Any]) -> int | None:
        return numbers.get(f"{machine}/{row.get('id') or row.get('claude_session_id')}")

    def resolve(self, session: Any) -> Any:
        """`session`, or the session elsewhere that its spoken number names."""
        match = SPOKEN_NUMBER.fullmatch(str(session).strip()) if isinstance(session, str) else None
        found = self.numbers.session_by_number(int(match.group(1))) if match and self.numbers else None
        return found if found and "/" in found else session

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
            recap, asks = await asyncio.wait_for(
                asyncio.gather(machine.call("fleet_recap", {}), self._approvals(machine)), ASK_SECONDS
            )
            rows = list((recap.structured_content or {}).get("sessions") or [])
            seen = {"state": "online", "error": None, "last_seen": now, "sessions": rows, "approvals": asks}
            seen["latency_ms"] = round((time.monotonic() - started) * 1000)
        except (TimeoutError, RuntimeError) as exc:
            seen = {**self._trouble(machine, exc, before, now), "last_seen": before.get("last_seen"), "sessions": []}
            seen["latency_ms"], seen["approvals"] = None, before.get("approvals", [])
        self._seen[name] = {**seen, "checked_at": now}
        self._tell(name, before, self._seen[name])

    @staticmethod
    async def _approvals(machine: machines.Machine) -> list[dict[str, Any]]:
        """The questions the machine's own bridge waits on; none when it cannot say."""
        try:
            answer = await machine.call("list_pending_approvals", {})
        except RuntimeError:
            return []
        return [] if answer.is_error else list((answer.structured_content or {}).get("approvals") or [])

    def _tell(self, name: str, before: dict[str, Any], now: dict[str, Any]) -> None:
        """Tell the page what changed on a machine since it was last asked."""
        if before.get("state", "online") != now["state"] or (not before and now["state"] != "online"):
            log.info("machine %s is %s", name, now["state"])
            if before:
                self.publish("machine", next(r for r in self.rows() if r["name"] == name))
        if now["state"] != "online":
            return  # what a machine away is doing is not known, so nothing else is said
        self._tell_sessions(name, before, now)
        self._tell_prompts(name, before, now)

    def _tell_sessions(self, name: str, before: dict[str, Any], now: dict[str, Any]) -> None:
        was = {str(r.get("id")): r for r in before.get("sessions", [])}
        for row in now["sessions"]:
            old = was.get(str(row.get("id")))
            if old is None or old.get("state") == row.get("state"):
                continue
            self.publish(
                "status",
                {
                    **self._page_row(name, row),
                    "session": f"{name}/{row.get('id')}",
                    "previous": old.get("state"),
                    "at": _now_iso(self.clock()),
                },
            )

    def _tell_prompts(self, name: str, before: dict[str, Any], now: dict[str, Any]) -> None:
        asked = {str(a.get("id")) for a in before.get("approvals", [])}
        open_now = {str(a.get("id")) for a in now["approvals"]}
        for prompt in self._prompts_of(name, now):
            if prompt["id"].partition("/")[2] not in asked:
                self.publish("prompt", prompt)
        for gone in sorted(asked - open_now):
            self._asked_at.pop(f"{name}/{gone}", None)
            self.publish("prompt_closed", {"id": f"{name}/{gone}", "how": "screen"})

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
        numbers = self.numbered()
        for m in self.machines:
            for row in self._seen.get(m.machine.name, {}).get("sessions", []):
                name = f"{m.machine.name}/{row.get('name')}"
                number = self._number_of(numbers, m.machine.name, row)
                out.append({**row, "name": name, "machine": m.machine.name, "number": number})
        return out

    # -- what the phone page reads --------------------------------------------------------
    def _page_row(self, machine: str, row: dict[str, Any], number: int | None = None) -> dict[str, Any]:
        out = {**row, "id": f"{machine}/{row.get('id')}", "machine": machine, "number": number}
        if row.get("kind") != "bridge-session":
            out["answer_in"] = "claude_app"  # its questions are answered through Remote Control
        return out

    def listing(self, local: dict[str, Any]) -> dict[str, Any]:
        """The hub's own session list for the page, with every reachable machine's added."""
        here = [{**row, "machine": None} for row in local.get("sessions", [])]
        numbers = self.numbered()
        there = [
            self._page_row(m.machine.name, row, self._number_of(numbers, m.machine.name, row))
            for m in self.machines
            for row in self._seen.get(m.machine.name, {}).get("sessions", [])
        ]
        return {**local, "sessions": [*here, *there], "machines": self.rows()}

    def _prompts_of(self, name: str, seen: dict[str, Any]) -> list[dict[str, Any]]:
        if seen.get("state") != "online":
            return []
        out = []
        for ask in seen.get("approvals", []):
            pid = f"{name}/{ask.get('id')}"
            asked_at = self._asked_at.setdefault(pid, _now_iso(self.clock()))
            out.append(
                {
                    "id": pid,
                    "session": f"{name}/{ask.get('session_id')}",
                    "machine": name,
                    "kind": "approval",
                    "tool": str(ask.get("tool", "")),
                    "input": str(ask.get("input", "")),
                    "cut": False,
                    "asked_at": asked_at,
                }
            )
        return out

    def prompts(self) -> list[dict[str, Any]]:
        """The questions on other machines the hub can answer, in the page's shape."""
        return [p for m in self.machines for p in self._prompts_of(m.machine.name, self._seen.get(m.machine.name, {}))]

    def _target(self, prefixed: str) -> tuple[machines.Machine, str]:
        target = self.split(prefixed)
        if target is None:
            raise LookupError(f"no session {prefixed}")
        return target

    async def output(self, session: str, limit: int, after: int | None) -> dict[str, Any]:
        machine, own = self._target(session)
        arguments: dict[str, Any] = {"session": own, "limit": limit}
        if after is not None:
            arguments["after"] = after
        try:
            answer = await machine.call("read_session_output", arguments)
        except RuntimeError as exc:
            raise LookupError(str(exc)) from None
        body = answer.structured_content or {}
        if answer.is_error:
            raise LookupError(str(body.get("error") or f"no session {session}"))
        return {k: body.get(k) for k in ("status", "turns", "next_after", "turns_left")}

    def pending(self, session: str) -> dict[str, Any]:
        machine, own = self._target(session)
        prompts = [p for p in self.prompts() if p["session"] == session]
        approvals = [{"id": p["id"], "number": None, "tool": p["tool"], "summary": p["input"]} for p in prompts]
        return {"waiting": bool(approvals), "question": None, "approvals": approvals}

    def detail(self, session: str) -> dict[str, Any]:
        """What the hub last heard about a session on another machine."""
        machine, own = self._target(session)
        name = machine.machine.name
        for row in self._seen.get(name, {}).get("sessions", []):
            if own in (str(row.get("id")), str(row.get("name"))):
                return self._page_row(name, row)
        raise LookupError(f"no session {session}")

    def detail_page(self, session: str) -> dict[str, Any]:
        """A session on another machine in the shape of the page's detail view. Its agent
        tells no more than its row, so the counts are empty rather than guessed."""
        row = self.detail(session)
        return {
            "session": row["id"],
            "number": None,
            "name": row.get("name"),
            "machine": row["machine"],
            "context": None,
            "tool_calls": {"total": 0, "by_tool": {}},
            "subagents": {"running": 0, "total": 0},
            "timeline": [],
            "summary": None,
            "summary_state": "none",
            "summary_error": None,
            "summary_at": None,
        }

    def active_row(self, session: str) -> dict[str, Any]:
        """A session on another machine as the one the owner talks to: its id and the name
        the voice reaches it by, both with the machine in front."""
        row = self.detail(session)
        name = f"{row['machine']}/{row.get('name')}"
        return {"id": row["id"], "name": name, "kind": row.get("kind"), "number": None, "machine": row["machine"]}

    async def answer(self, prompt: str, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        """Pass a yes or no from the page to the machine the question is on."""
        target = self.split(prompt)
        if target is None or prompt not in {p["id"] for p in self.prompts()}:
            return 404, {"error": "this prompt is gone"}
        machine, own = target
        decision = body.get("decision")
        if decision not in ("allow", "deny"):
            return 409, {"error": "an approval is answered with decision allow or deny"}
        arguments: dict[str, Any] = {"approval_id": own}
        if decision == "deny" and body.get("reason"):
            arguments["reason"] = str(body["reason"])
        try:
            # RULE: an answer from the page reaches the machine the question is on
            answer = await machine.call("approve" if decision == "allow" else "deny", arguments)
        except RuntimeError:
            return 502, {"error": f"{machine.machine.name} did not answer"}
        if answer.is_error:
            return 404, {"error": "this prompt is gone"}
        seen = self._seen.get(machine.machine.name, {})
        seen["approvals"] = [a for a in seen.get("approvals", []) if str(a.get("id")) != own]
        self._asked_at.pop(prompt, None)
        self.publish("prompt_closed", {"id": prompt, "how": "phone"})
        return 200, {"ok": True}


def _result(body: dict[str, Any]) -> Any:
    from mcp.types import CallToolResult, TextContent

    return CallToolResult(content=[TextContent(type="text", text=json.dumps(body))], structured_content=body)


def _structured(result: Any) -> dict[str, Any]:
    """A tool's structured answer, whichever form the server hands it on in: a result
    object, or the plain dict the MCP server builds from it."""
    if isinstance(result, dict):
        return dict(result.get("structuredContent") or result.get("structured_content") or {})
    return dict(getattr(result, "structured_content", None) or {})


def _failed(result: Any) -> bool:
    return bool(result.get("isError") if isinstance(result, dict) else getattr(result, "is_error", False))


def _named_back(body: Any, machine: str, own: str) -> Any:
    """A machine's answer with the session's name as the hub knows it."""
    if not isinstance(body, dict):
        return body
    out = dict(body)
    for key in ("session", "name"):
        if out.get(key) == own:
            out[key] = f"{machine}/{own}"
    return out


def _routed(fleet: Fleet, tool: str, arguments: dict[str, Any]) -> tuple[str | None, Any]:
    """The argument naming the session, and its machine if that machine answers the tool.
    A spoken number is turned into the session's name first."""
    key = ROUTED.get(tool)
    if not key:
        return None, None
    arguments[key] = fleet.resolve(arguments.get(key))
    target = fleet.split(arguments.get(key))
    # RULE: a session on another machine is made the active one by its number
    if target is not None and tool not in target[0].spec.tools:
        return key, None  # the hub answers it itself, given the full name
    # RULE: a session on another machine is reached by the machine name in front
    return key, fleet.split(arguments.get(key))


def middleware(fleet: Fleet) -> Any:
    """Pass a call that names a session elsewhere to its machine, and add the other
    machines' sessions and states to the tools that look across sessions."""

    async def across(ctx: Any, call_next: Callable[[Any], Awaitable[Any]]) -> Any:
        params = ctx.params if isinstance(ctx.params, dict) else {}
        tool = str(params.get("name", ""))
        if ctx.method != "tools/call" or not fleet.machines:
            return await call_next(ctx)
        arguments = dict(params.get("arguments") or {})
        key, target = _routed(fleet, tool, arguments)
        ctx.params = {**params, "arguments": arguments}  # with a spoken number turned into its name
        if target is not None and key is not None:
            machine, own = target
            try:
                answer = await machine.call(tool, {**arguments, key: own})
            except RuntimeError as exc:
                from mcp.types import CallToolResult, TextContent

                return CallToolResult(content=[TextContent(type="text", text=str(exc))], is_error=True)
            if answer.is_error:
                return answer
            return _result(_named_back(machine.shown(answer.structured_content), machine.machine.name, own))
        result = await call_next(ctx)
        if tool in (*LISTS, "health") and not _failed(result):
            await fleet.refresh()
            body = dict(_structured(result))
            if tool in LISTS:
                body["sessions"] = [*body.get("sessions", []), *fleet.sessions()]
            body["machines"] = fleet.rows()
            with contextlib.suppress(Exception):
                return _result(body)
        return result

    return across
