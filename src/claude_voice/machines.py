"""Other machines: the hub's side of each `claude-voice agent`, and what the owner approved of it.

A machine is named in the settings file's ``[machines.<name>]`` section with its tailnet
address (ADR-CV-006). ``claude-voice machines approve <name>`` pairs with its agent: the owner
types the code the agent shows, sees what it offers, and types the machine's name. The hub
keeps the key the agent handed out, readable by its owner alone, and the approval: which
tailnet node it is, and what each tool takes.

From then on its tools are offered to the voice as ``<machine>_<tool>``. Every connection
first asks Tailscale who is at the address, and checks the tools are still the ones
approved; a machine that does not answer within two seconds is reported, not waited for.
"""

from __future__ import annotations

import contextlib
import copy
import json
import logging
import os
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

from . import access, invocation, plugins, tailnet
from .config import MachineSpec, PluginSpec

log = logging.getLogger("claude_voice")

FOLDER = Path("~/.config/claude-voice")
CONNECT_SECONDS = 2.0

Http = Callable[[MachineSpec, dict[str, str]], Any]
Whois = Callable[[str], "tuple[str, str] | None"]


def url(spec: MachineSpec) -> str:
    return f"http://{spec.address}:{spec.port}"


def http_client(spec: MachineSpec, headers: dict[str, str]) -> Any:
    """How the hub reaches an agent: plain HTTP over the tailnet, two seconds to connect."""
    import httpx

    return httpx.AsyncClient(
        base_url=url(spec), headers=headers, timeout=httpx.Timeout(spec.timeout, connect=CONNECT_SECONDS)
    )


def _key_file(folder: Path, name: str) -> Path:
    return folder.expanduser() / "machines" / name / "key"


def _approvals_file(folder: Path) -> Path:
    return folder.expanduser() / "machines-approved.json"


def _approvals(folder: Path) -> dict[str, Any]:
    try:
        return dict(json.loads(_approvals_file(folder).read_text()))
    except (OSError, ValueError):
        return {}


def _write_private(target: Path, text: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    spare = target.with_name(target.name + ".tmp")
    fd = os.open(spare, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text)
    os.replace(spare, target)


def tools_fingerprint(listing: dict[str, dict[str, Any]]) -> str:
    """What the owner approved the machine to offer: each tool's name and what it takes."""
    return plugins._hash({tool: found.get("schema") for tool, found in sorted(listing.items())})


@contextlib.asynccontextmanager
async def _session(spec: MachineSpec, key: str, http: Http) -> AsyncIterator[Any]:
    from mcp.client import Client
    from mcp.client.streamable_http import streamable_http_client

    async with (
        http(spec, {"Authorization": f"Bearer {key}"}) as h,
        Client(streamable_http_client(f"{url(spec)}/mcp", http_client=h)) as client,
    ):
        yield client


async def _listing(client: Any) -> dict[str, dict[str, Any]]:
    return {
        t.name: {"description": t.description or "", "schema": t.input_schema}
        for t in (await client.list_tools()).tools
    }


async def _whois(whois: Whois, address: str) -> tuple[str, str] | None:
    import anyio.to_thread

    return await anyio.to_thread.run_sync(whois, address)


async def pair(
    spec: MachineSpec,
    ask: Callable[[str], str] = input,
    say: Callable[[str], None] = print,
    http: Http = http_client,
    whois: Whois = tailnet.whois,
    folder: Path = FOLDER,
) -> dict[str, Any]:
    """Pair with the agent on `spec`: the owner types its code, sees what it offers, types its name."""
    import httpx

    node = await _whois(whois, spec.address)
    if node is None:
        say(f"{spec.address} is not a machine on your tailnet. Is Tailscale up on {spec.name}?")
        return {"approved": False}
    say(f"Machine {spec.name}: {node[1]} on your tailnet, at {spec.address}")
    code = ask(f"Pairing code shown on {node[1]} by `claude-voice agent pair`: ").strip().upper()
    try:
        async with http(spec, {}) as h:
            answer = await h.post("/pair", json={"code": code})
        if answer.status_code != 200:
            say(f"{node[1]} refused the code: {answer.json().get('error', answer.status_code)}")
            return {"approved": False}
        key = str(answer.json()["key"])
        async with _session(spec, key, http) as client:
            listing = await _listing(client)
    except (OSError, ValueError, KeyError, httpx.HTTPError) as exc:
        say(f"Could not reach the agent on {node[1]} ({exc}). Is it awake, and `claude-voice agent` running there?")
        return {"approved": False}
    say("  the voice may call:")
    for tool, found in sorted(listing.items()):
        say(f"    {plugins.exposed_name(spec.name, tool)}, takes {plugins.takes(found['schema'])}")
    # RULE: a machine is approved by typing its pairing code and then its name
    if ask(f"Type {spec.name} to approve it, anything else to stop: ").strip() != spec.name:
        say("Not approved.")
        return {"approved": False}
    _write_private(_key_file(folder, spec.name), key + "\n")
    approval = {"node": node[0], "seen_as": node[1], "address": spec.address, "listing": listing}
    _write_private(_approvals_file(folder), json.dumps({**_approvals(folder), spec.name: approval}, indent=2) + "\n")
    log.info("machine %s (%s) approved with %d tools", spec.name, node[1], len(listing))
    say(f"Approved. Restart the bridge to offer {spec.name}'s tools.")
    return {"approved": True, "tools": sorted(listing)}


def state(spec: MachineSpec, folder: Path = FOLDER) -> str:
    """Whether `spec` is approved, in a few words."""
    approval = _approvals(folder).get(spec.name)
    if not isinstance(approval, dict) or not _key_file(folder, spec.name).exists():
        return f"not approved; run: {invocation.command()} machines approve {spec.name}"
    if approval.get("address") != spec.address:
        return f"approved at another address; run: {invocation.command()} machines approve {spec.name}"
    tools = ", ".join(plugins.exposed_name(spec.name, t) for t in sorted(approval.get("listing", {})))
    return f"approved, as {approval.get('seen_as')}; tools {tools}"


class Machine(plugins.Plugin):
    """One other machine, reached through its agent the way a plugin is reached through its
    process: connected on first use, kept, and connected again after a failure."""

    # RULE: a machine that does not answer in two seconds is reported, not waited for
    start_seconds = CONNECT_SECONDS

    def __init__(self, spec: MachineSpec, approval: dict[str, Any], key: str, http: Http, whois: Whois) -> None:
        listing = approval["listing"]
        as_plugin = PluginSpec(spec.name, "", [], "", sorted(listing), spec.timeout)
        super().__init__(as_plugin, lambda params: None)
        self.machine, self.approval, self.key, self.http, self.whois = spec, approval, key, http, whois
        self.access = access.Held()

    @contextlib.asynccontextmanager
    async def open(self) -> AsyncIterator[Any]:
        name = self.machine.name
        node = await _whois(self.whois, self.machine.address)
        # RULE: the hub talks only to the machine Tailscale says it approved
        if node is None or node[0] != self.approval["node"]:
            raise Refused(f"{name} at {self.machine.address} is not the machine you approved")
        async with _session(self.machine, self.key, self.http) as client:
            # RULE: a machine whose tools changed is not called until approved again
            if tools_fingerprint(await _listing(client)) != tools_fingerprint(self.approval["listing"]):
                raise Refused(f"{name} now offers other tools than you approved")
            await self.read_access(client)
            yield client

    async def read_access(self, client: Any) -> bool:
        """Read what the machine allows now from its agent_info; False when it gave no answer."""
        try:
            answer = await client.call_tool("agent_info", {})
        except Exception as exc:
            log.info("machine %s: agent_info gave no answer: %s", self.machine.name, exc)
            return False
        self.access = access.read(answer.structured_content)
        return True

    async def _refresh(self) -> bool:
        try:
            answer = await plugins.Plugin.call(self, "agent_info", {})
        except RuntimeError:
            return False
        self.access = access.read(answer.structured_content)
        return True

    async def call(self, tool: str, arguments: dict[str, Any]) -> Any:
        """The tool's answer, with a warning when a use of its plugin was last reported off.
        The warning never stops the call: the tool's own answer decides."""
        try:
            answer = await super().call(tool, arguments)
        except RuntimeError as exc:
            warning = await self._warning(tool)
            raise RuntimeError(f"{exc}. {warning}" if warning else str(exc)) from None
        warning = await self._warning(tool)
        return _with_warning(answer, warning) if warning else answer

    async def _warning(self, tool: str) -> str | None:
        """After a call, what to warn about, from a fresh read when the tool was held as off."""
        # RULE: a call to a tool held as off reads access again
        fresh = await self._refresh() if self.access.off(tool) else True
        return self.access.warning(self.machine.name, tool, fresh)

    async def permissions(self) -> dict[str, Any]:
        """Which permissions the machine's plugins have, read fresh when it answers."""
        # RULE: the owners question reads access fresh
        fresh = await self._refresh()
        return self.access.answer(self.machine.name, fresh)

    def shown(self, answer: Any) -> Any:
        """The machine's answer without its own session numbers, which only it knows."""
        return without_numbers(answer)

    def trouble(self, exc: Exception) -> str:
        name = self.machine.name
        refused = _refusal(exc)
        if refused is not None:
            return f"{refused}; to approve it again, run on the hub: {invocation.command()} machines approve {name}"
        return f"{name} did not answer; it may be asleep, offline, or not running `claude-voice agent`"


def _with_warning(answer: Any, warning: str) -> Any:
    """The answer with the warning added: as a field of its object, or a line of its text."""
    shown = copy.copy(answer)
    if answer.is_error:
        shown.content = _with_line(answer, warning)
        return shown
    if answer.structured_content is not None:
        shown.structured_content = {**answer.structured_content, "warning": warning}
        return shown
    shown.content = _with_line(answer, warning)
    found = plugins.as_object(_text(answer))
    if found:
        shown.structured_content = {**found, "warning": warning}
    return shown


def _with_line(answer: Any, line: str) -> list[Any]:
    """The answer's content with one more line of text after it."""
    from mcp.types import TextContent

    return [*(answer.content or []), TextContent(type="text", text=line)]


def _text(answer: Any) -> str:
    """The text of the answer's content, one line per part."""
    return "\n".join(getattr(c, "text", "") for c in answer.content or [] if getattr(c, "text", None))


def without_numbers(body: Any) -> Any:
    """`body` with every `number` taken out. Another machine numbers its sessions from a
    pool of its own, so its 1 may be the hub's own 1; only the hub's numbers are spoken."""
    if isinstance(body, dict):
        # RULE: a session number from another machine is never passed on
        return {k: without_numbers(v) for k, v in body.items() if k != "number"}
    if isinstance(body, list):
        return [without_numbers(v) for v in body]
    return body


class Refused(ConnectionError):
    """A machine that answered, but not as the one the owner approved."""


def _refusal(exc: BaseException) -> Refused | None:
    """The refusal in `exc`, which the MCP client may have wrapped in a group."""
    if isinstance(exc, Refused):
        return exc
    for inner in getattr(exc, "exceptions", ()):
        found = _refusal(inner)
        if found is not None:
            return found
    return None


def start_approved(
    server: Any,
    specs: list[MachineSpec],
    folder: Path = FOLDER,
    http: Http = http_client,
    whois: Whois = tailnet.whois,
) -> list[Machine]:
    """Offer each approved machine's tools on `server`, as approved: nothing is asked of the
    machine until the voice calls one, so a machine that is asleep never slows the start."""
    approvals = _approvals(folder)
    started = []
    for spec in specs:
        approval = approvals.get(spec.name)
        try:
            key = _key_file(folder, spec.name).read_text().strip()
        except OSError:
            key = ""
        if not isinstance(approval, dict) or not key or approval.get("address") != spec.address:
            run = invocation.command()
            log.info("machine %s is not approved; run: %s machines approve %s", spec.name, run, spec.name)
            continue
        machine = Machine(spec, approval, key, http, whois)
        machine.offered = plugins.offer(server, machine, approval["listing"])
        _offer_permissions(server, machine)
        started.append(machine)
        log.info("machine %s offers %s", spec.name, ", ".join(machine.offered))
    return started


def _offer_permissions(server: Any, machine: Machine) -> None:
    """Offer `<machine>_permissions`, which answers "which permissions does <machine> have?"."""
    from mcp.types import ToolAnnotations

    name = plugins.exposed_name(machine.machine.name, "permissions")
    if name in machine.offered:
        return
    title = f"Which permissions {machine.machine.name} has"
    marks = ToolAnnotations(read_only_hint=True)

    async def permissions() -> dict[str, Any]:
        """Which private things each plugin on the machine may use now, read fresh from it.
        Advice only: a tool's own answer decides."""
        return await machine.permissions()

    server.tool(name=name, title=title, annotations=marks)(permissions)
    machine.offered.append(name)
