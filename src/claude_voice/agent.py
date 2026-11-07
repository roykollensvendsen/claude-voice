"""The agent: claude-voice on another machine, answering only the hub it was paired with.

Roy's laptop is the hub; the voice talks to it alone. Each other machine runs
``claude-voice agent``, a small MCP server listening on that machine's tailnet address and
nowhere else (ADR-CV-006). It offers the plugins its own owner approved, and nothing more.

Pairing: ``claude-voice agent pair`` shows an eight-character code for ten minutes. The hub
sends it to ``/pair``; the agent answers once with a long random key and remembers the
hub's tailnet identity. From then on every call needs both: that key, and a caller the
tailnet says is that hub. Only a hash of the code and of the key is kept on disk.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import logging
import os
import secrets
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from starlette.types import ASGIApp, Receive, Scope, Send

from .config import PluginSpec

log = logging.getLogger("claude_voice")

PORT = 8812
STATE = Path("~/.config/claude-voice/agent")
CODE_LETTERS = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # nothing that reads as another letter aloud
CODE_SECONDS = 10 * 60
CODE_TRIES = 5
TAILNET = ipaddress.ip_network("100.64.0.0/10")

Whois = Callable[[str], "tuple[str, str] | None"]


class AgentError(RuntimeError):
    pass


def _tailscale_ip() -> str:
    return subprocess.run(["tailscale", "ip", "-4"], check=True, capture_output=True, text=True, timeout=5).stdout


def listen_address(ask: Callable[[], str] = _tailscale_ip) -> str:
    """This machine's tailnet address, the only one the agent listens on."""
    try:
        answer = ask().strip().splitlines()
    except (OSError, subprocess.SubprocessError):
        raise AgentError("Tailscale is not running here; install it and run: sudo tailscale up") from None
    # RULE: the agent listens on its tailnet address only
    try:
        address = ipaddress.ip_address(answer[0]) if answer else None
    except ValueError:
        address = None
    if address is None or address not in TAILNET:
        raise AgentError("this machine has no tailnet address; run: sudo tailscale up")
    return str(address)


def tailscale_whois(address: str) -> tuple[str, str] | None:
    """(stable node id, machine name) of the tailnet node at `address`, or None."""
    try:
        ipaddress.ip_address(address)
        out = subprocess.run(
            ["tailscale", "whois", "--json", address], check=True, capture_output=True, text=True, timeout=5
        ).stdout
        node = json.loads(out)["Node"]
        return str(node["StableID"]), str(node.get("ComputedName") or node["Name"].split(".")[0])
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        return None


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


class Pairing:
    """The pairing code while one is open, and the paired hub: its key's hash and identity."""

    def __init__(self, folder: Path = STATE, clock: Callable[[], float] = time.time) -> None:
        self.folder, self.clock = folder.expanduser(), clock

    def _read(self, name: str) -> dict[str, Any]:
        try:
            return dict(json.loads((self.folder / name).read_text()))
        except (OSError, ValueError):
            return {}

    def _write(self, name: str, body: dict[str, Any] | None) -> None:
        target = self.folder / name
        if body is None:
            target.unlink(missing_ok=True)
            return
        self.folder.mkdir(parents=True, exist_ok=True)
        os.chmod(self.folder, 0o700)
        spare = target.with_name(name + ".tmp")
        fd = os.open(spare, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(json.dumps(body) + "\n")
        os.replace(spare, target)

    def start(self) -> str:
        """A new code, open for ten minutes; an older open code stops working."""
        code = "".join(secrets.choice(CODE_LETTERS) for _ in range(8))
        self._write("pairing.json", {"code": _hash(code), "until": self.clock() + CODE_SECONDS, "tries": 0})
        log.info("agent: pairing code shown, open for %d minutes", CODE_SECONDS // 60)
        return code

    def redeem(self, code: str, hub: tuple[str, str]) -> str | None:
        """The new key for the hub that sent the right code in time, once; else None."""
        open_ = self._read("pairing.json")
        # RULE: a pairing code works once, for ten minutes
        if not open_ or self.clock() > float(open_.get("until", 0)):
            self._write("pairing.json", None)
            return None
        if not hmac.compare_digest(_hash(code.strip().upper()), str(open_.get("code"))):
            tries = int(open_.get("tries", 0)) + 1
            # RULE: five wrong pairing codes end the pairing
            self._write("pairing.json", None if tries >= CODE_TRIES else {**open_, "tries": tries})
            log.info("agent: wrong pairing code from %s (%d of %d)", hub[1], tries, CODE_TRIES)
            return None
        self._write("pairing.json", None)
        key = secrets.token_urlsafe(32)
        self._write("hub.json", {"key": _hash(key), "node": hub[0], "name": hub[1], "paired_at": self.clock()})
        log.info("agent: paired with hub %s (%s)", hub[1], hub[0])
        return key

    def hub(self) -> dict[str, Any]:
        """The paired hub's identity, without its key: {} when none is paired."""
        found = self._read("hub.json")
        return {"node": found["node"], "name": found["name"]} if found.get("node") else {}

    def allows(self, key: str, node: str | None) -> bool:
        # RULE: a call needs the key and the tailnet identity of the paired hub
        found = self._read("hub.json")
        if not found.get("key") or node is None:
            return False
        right_key = hmac.compare_digest(_hash(key), str(found["key"]))
        return right_key and hmac.compare_digest(node, str(found.get("node")))


def build_agent(
    machine: str,
    pairing: Pairing,
    specs: list[PluginSpec] | None = None,
    plugin_approvals: Path | None = None,
    plugin_client: Callable[[Any], Any] | None = None,
    address: str = "127.0.0.1",
) -> Any:
    """The agent's MCP server: what this machine's owner approved, and a line about itself."""
    from mcp.server import MCPServer
    from mcp.types import ToolAnnotations

    from . import metrics, plugins

    mcp = MCPServer(
        f"claude-voice-agent-{machine}",
        title=f"claude-voice on {machine}",
        version=metrics.version(),
        instructions=f"The claude-voice agent on {machine}. Only its paired hub may call it.",
    )
    started = plugins.start_approved(mcp, specs or [], plugin_approvals, plugin_client)
    offered = [name for plugin in started for name in plugin.offered]

    @mcp.tool(title="About this machine", annotations=ToolAnnotations(read_only_hint=True))
    def agent_info() -> dict[str, Any]:
        """Which machine this is, the hub it answers, and the tools it offers."""
        return {
            "machine": machine,
            "address": address,
            "hub": pairing.hub().get("name"),
            "tools": offered,
            "version": metrics.version(),
        }

    return mcp


class Gate:
    """In front of the agent: `/pair` takes a code from the tailnet, everything else needs
    the paired hub's key from the paired hub's address."""

    def __init__(self, app: ASGIApp, pairing: Pairing, whois: Whois) -> None:
        self.app, self.pairing, self.whois = app, pairing, whois
        self.server: Any = None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        from starlette.requests import Request
        from starlette.responses import JSONResponse

        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        import anyio.to_thread

        request = Request(scope, receive)
        caller = request.client.host if request.client else ""
        node = await anyio.to_thread.run_sync(self.whois, caller) if _on_tailnet(caller) else None
        if scope.get("path") == "/pair" and request.method == "POST":
            response = await self._pair(request, node)
        elif self.pairing.allows(_bearer(request), node[0] if node else None):
            await self.app(scope, receive, send)
            return
        else:
            log.info("agent: refused a call from %s", caller or "?")
            response = JSONResponse({"error": "not the paired hub"}, 401, {"WWW-Authenticate": "Bearer"})
        await response(scope, receive, send)

    async def _pair(self, request: Any, node: tuple[str, str] | None) -> Any:
        from starlette.responses import JSONResponse

        if node is None:
            return JSONResponse({"error": "pair from a machine on the tailnet"}, 403)
        try:
            code = str((await request.json()).get("code", ""))
        except ValueError:
            code = ""
        key = self.pairing.redeem(code, node)
        if key is None:
            return JSONResponse({"error": "no open pairing with that code; run: claude-voice agent pair"}, 403)
        return JSONResponse({"key": key, "hub": node[1]})


def _on_tailnet(address: str) -> bool:
    try:
        return ipaddress.ip_address(address) in TAILNET
    except ValueError:
        return False


def _bearer(request: Any) -> str:
    scheme, _, value = request.headers.get("authorization", "").partition(" ")
    return value.strip() if scheme.lower() == "bearer" else ""


def build_agent_app(server: Any, pairing: Pairing, whois: Whois = tailscale_whois, address: str = "127.0.0.1") -> Any:
    """The agent's HTTP app, answering only requests addressed to its tailnet address."""
    from mcp.server.transport_security import TransportSecuritySettings

    security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True, allowed_hosts=[f"{address}:*"], allowed_origins=[]
    )
    gate = Gate(server.streamable_http_app(transport_security=security), pairing, whois)
    gate.server = server  # its session manager runs in the app's lifespan; tests run it themselves
    return gate


def serve(machine: str, specs: list[PluginSpec], pairing: Pairing | None = None) -> None:
    """Run the agent on this machine's tailnet address until stopped."""
    import uvicorn

    address = listen_address()
    pairing = pairing or Pairing()
    app = build_agent_app(build_agent(machine, pairing, specs, address=address), pairing, address=address)
    log.info("agent %s listening on %s:%d", machine, address, PORT)
    uvicorn.Server(uvicorn.Config(app, host=address, port=PORT, log_level="info", log_config=None)).run()
