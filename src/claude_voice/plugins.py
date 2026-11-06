"""Plugins: other local MCP servers whose tools the voice may call through the bridge.

A plugin is named in the settings file's ``[plugins.<name>]`` section (its command, folder
and the tools allowed to the voice), and runs only once the owner has approved it by typing
its name (``claude-voice plugins approve <name>``). The approval is two fingerprints: one of
the command, folder and tools, checked before the plugin is started, and one of what each
allowed tool takes, checked once it has said so. A change to either needs approving again. Each plugin runs as its
own long-lived child process, started again with a growing pause when it fails; every call
has a time limit. Only the allowed tools are offered to the voice, named after the plugin,
and the plugin's own safety rules stay in the plugin.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import os
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .config import PluginSpec

log = logging.getLogger("claude_voice")
APPROVALS = Path("~/.config/claude-voice/plugins-approved.json")
START_SECONDS = 15.0  # how long a plugin may take to start


def _hash(shape: Any) -> str:
    return hashlib.sha256(json.dumps(shape, sort_keys=True).encode()).hexdigest()


def fingerprint(spec: PluginSpec) -> str:
    """What the owner approved to run: the command, folder and tools, never anything secret."""
    return _hash({"command": spec.command, "args": spec.args, "cwd": spec.cwd, "tools": sorted(spec.tools)})


def input_fingerprint(spec: PluginSpec, listing: dict[str, dict[str, Any]]) -> str:
    """What the owner approved each allowed tool to take, as the plugin described it."""
    return _hash({tool: listing.get(tool, {}).get("schema") for tool in sorted(spec.tools)})


def _approvals(path: Path) -> dict[str, Any]:
    try:
        return dict(json.loads(path.expanduser().read_text()))
    except (OSError, ValueError):
        return {}


def _approval(spec: PluginSpec, path: Path | None) -> dict[str, Any]:
    found = _approvals(path or APPROVALS).get(spec.name)
    return found if isinstance(found, dict) else {}  # an approval from before inputs counted is none


def approved(spec: PluginSpec, path: Path | None = None) -> bool:
    # RULE: a plugin runs only as the owner approved it
    return _approval(spec, path).get("runs") == fingerprint(spec)


def inputs_approved(spec: PluginSpec, listing: dict[str, dict[str, Any]], path: Path | None = None) -> bool:
    # RULE: a plugin tool whose inputs changed is not offered until approved again
    return _approval(spec, path).get("inputs") == input_fingerprint(spec, listing)


def takes(schema: Any) -> str:
    """What a tool takes, in a few words: its inputs' names, a ? on the optional ones."""
    properties = (schema or {}).get("properties") or {}
    needed = set((schema or {}).get("required") or [])
    return ", ".join(name if name in needed else f"{name}?" for name in properties) or "nothing"


def approve(spec: PluginSpec, listing: dict[str, dict[str, Any]], path: Path | None = None) -> None:
    target = (path or APPROVALS).expanduser()
    body = {**_approvals(target), spec.name: {"runs": fingerprint(spec), "inputs": input_fingerprint(spec, listing)}}
    target.parent.mkdir(parents=True, exist_ok=True)
    spare = target.with_name(target.name + ".tmp")
    spare.write_text(json.dumps(body, indent=2) + "\n")
    os.chmod(spare, 0o600)
    os.replace(spare, target)
    log.info("plugin %s approved", spec.name)


def exposed_name(plugin: str, tool: str) -> str:
    """The name the voice sees: the plugin's name in front, once."""
    return tool if tool.startswith(f"{plugin}_") else f"{plugin}_{tool}"


def CLIENT(params: Any) -> Any:  # noqa: N802 - a stand-in for the MCP client class, replaced in tests
    """How the bridge talks to a plugin: the MCP library's own client."""
    from mcp.client import Client

    return Client(params)


def parameters(spec: PluginSpec) -> Any:
    """How to start the plugin. The program is looked up the way the bridge finds its own tools
    (the service's PATH is short), and the child gets the MCP library's small default
    environment: nothing of the bridge's own, so no key or token passes through it."""
    from mcp.client.stdio import StdioServerParameters

    from . import procs

    program = spec.command if os.path.isabs(spec.command) else procs.find(spec.command) or spec.command
    return StdioServerParameters(command=program, args=spec.args, cwd=spec.cwd)


class Plugin:
    """One plugin's child process: started on first use, kept, and started again after a failure."""

    def __init__(self, spec: PluginSpec, client_factory: Callable[[Any], Any]) -> None:
        self.spec, self.client_factory = spec, client_factory
        self.state = "not started"
        self.instructions: str | None = None
        self._client: Any = None
        self._lock = asyncio.Lock()
        self._runner: asyncio.Task[None] | None = None
        self._ready = asyncio.Event()
        self._closing = asyncio.Event()

    def listing(self) -> dict[str, dict[str, Any]]:
        """The allowed tools as the plugin describes them, read once at start in a thread of
        its own, so the voice sees each tool's real description and arguments."""
        found: dict[str, dict[str, Any]] = {}

        async def read() -> None:
            async with self.client_factory(parameters(self.spec)) as client:
                self.instructions = getattr(client, "instructions", None) or None
                for tool in (await client.list_tools()).tools:
                    if tool.name in self.spec.tools:
                        found[tool.name] = {"description": tool.description or "", "schema": tool.input_schema}

        def run() -> None:
            try:
                asyncio.run(asyncio.wait_for(read(), START_SECONDS))
            except Exception as exc:  # a plugin that cannot start yet still gets its tools, plainly described
                log.info("plugin %s could not be listed: %s", self.spec.name, exc)
                self.state = "failed to start"

        worker = threading.Thread(target=run, daemon=True)
        worker.start()
        worker.join(START_SECONDS + 5)
        return found

    async def _run(self) -> None:
        """Hold the connection open in a task of its own: the client must be closed in the
        task that opened it."""
        try:
            async with self.client_factory(parameters(self.spec)) as client:
                self._client, self.state = client, "running"
                self._ready.set()
                log.info("plugin %s started", self.spec.name)
                await self._closing.wait()
        except Exception as exc:
            log.info("plugin %s ended: %s", self.spec.name, exc)
        finally:
            self._client = None
            self._ready.set()  # wake a caller waiting for a start that failed

    async def _connect(self) -> Any:
        if self._runner is None or self._runner.done():
            self._ready, self._closing = asyncio.Event(), asyncio.Event()
            self._runner = asyncio.create_task(self._run())
        await asyncio.wait_for(self._ready.wait(), START_SECONDS)
        if self._client is None:
            raise ConnectionError(f"{self.spec.name} did not start")
        return self._client

    async def _drop(self) -> None:
        self._closing.set()
        runner = self._runner
        if runner is not None:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(asyncio.shield(runner), 5)

    async def call(self, tool: str, arguments: dict[str, Any]) -> Any:
        """The plugin's answer, or RuntimeError saying what went wrong; a failed process is
        dropped, so the next call starts it again."""
        async with self._lock:
            try:
                client = await self._connect()
                # RULE: a plugin call that runs too long is cut off
                return await asyncio.wait_for(client.call_tool(tool, arguments), self.spec.timeout)
            except TimeoutError:
                await self._drop()
                self.state = "restarting"
                raise RuntimeError(f"{self.spec.name} took longer than {self.spec.timeout:g} s") from None
            except Exception as exc:
                await self._drop()
                self.state = "restarting"
                log.info("plugin %s failed: %s", self.spec.name, exc)
                raise RuntimeError(f"{self.spec.name} stopped working; it starts again on the next call") from None

    def status(self) -> dict[str, Any]:
        return {"name": self.spec.name, "state": self.state, "tools": list(self.spec.tools)}


def _as_object(text: str) -> dict[str, Any]:
    """A plugin's text answer as an object: its JSON when it is an object, else the text."""
    with contextlib.suppress(ValueError):
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    return {"text": text}


def offer(server: Any, plugin: Plugin, listing: dict[str, dict[str, Any]]) -> list[str]:
    """Offer the plugin's allowed tools on the bridge's MCP server; the names offered."""
    from mcp.server.mcpserver.exceptions import ToolError
    from mcp.server.mcpserver.tools.base import Tool

    class PluginTool(Tool):
        async def run(self, arguments: dict[str, Any], context: Any, convert_result: bool = False) -> Any:
            try:
                answer = await plugin.call(self.meta["plugin_tool"], arguments)  # type: ignore[index]
            except RuntimeError as exc:
                raise ToolError(str(exc)) from None
            text = "\n".join(getattr(c, "text", "") for c in answer.content or [] if getattr(c, "text", None))
            if answer.is_error:
                raise ToolError(text or f"{plugin.spec.name} refused")
            result = answer.structured_content if answer.structured_content is not None else _as_object(text)
            return self.fn_metadata.convert_result(result) if convert_result else result

    async def placeholder() -> dict[str, Any]:  # never called: PluginTool.run answers instead
        return {}

    offered = []
    for tool in plugin.spec.tools:
        name = exposed_name(plugin.spec.name, tool)
        known = listing.get(tool, {})
        description = known.get("description") or f"{tool}, from the {plugin.spec.name} plugin"
        if plugin.instructions:  # the plugin's own rules travel with every tool, whatever the client reads
            description = f"{description}\n\n{plugin.spec.name} rules: {plugin.instructions}"
        base = Tool.from_function(placeholder, name=name, description=description, structured_output=True)
        fields = {k: getattr(base, k) for k in type(base).model_fields}
        made = PluginTool(**{**fields, "meta": {"plugin": plugin.spec.name, "plugin_tool": tool}})
        made.parameters = known.get("schema") or {"type": "object", "properties": {}}
        server._tool_manager._tools[name] = made
        offered.append(name)
    # RULE: a plugins own instructions reach the voice
    if plugin.instructions:
        lowlevel = server._lowlevel_server
        lowlevel.instructions = (
            f"{lowlevel.instructions or ''}\n\nThe {plugin.spec.name} plugin: {plugin.instructions}".strip()
        )
    return offered
