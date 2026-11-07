"""Other machines: the hub's side of each `claude-voice agent`, and what Roy approved of it.

A machine is named in the settings file's ``[machines.<name>]`` section with its tailnet
address (ADR-CV-006). ``claude-voice machines approve <name>`` pairs with its agent: Roy
types the code the agent shows, sees what it offers, and types the machine's name. The hub
keeps the key the agent handed out, readable by its owner alone, and the approval: which
tailnet node it is, and what each tool takes.

From then on its tools are offered to the voice as ``<machine>_<tool>``. Every connection
first asks Tailscale who is at the address, and checks the tools are still the ones
approved; a machine that does not answer within two seconds is reported, not waited for.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

from . import plugins, tailnet
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
    """What Roy approved the machine to offer: each tool's name and what it takes."""
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
    """Pair with the agent on `spec`: Roy types its code, sees what it offers, types its name."""
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
    except (OSError, ValueError, KeyError) as exc:
        say(f"Could not reach the agent on {node[1]}: {exc}. Is `claude-voice agent` running there?")
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
        return f"not approved; run: claude-voice machines approve {spec.name}"
    if approval.get("address") != spec.address:
        return f"approved at another address; run: claude-voice machines approve {spec.name}"
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
            yield client

    def trouble(self, exc: Exception) -> str:
        name = self.machine.name
        refused = _refusal(exc)
        if refused is not None:
            return f"{refused}; to approve it again, run on the hub: claude-voice machines approve {name}"
        return f"{name} did not answer; it may be asleep, offline, or not running `claude-voice agent`"


class Refused(ConnectionError):
    """A machine that answered, but not as the one Roy approved."""


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
            log.info("machine %s is not approved; run: claude-voice machines approve %s", spec.name, spec.name)
            continue
        machine = Machine(spec, approval, key, http, whois)
        machine.offered = plugins.offer(server, machine, approval["listing"])
        started.append(machine)
        log.info("machine %s offers %s", spec.name, ", ".join(machine.offered))
    return started
