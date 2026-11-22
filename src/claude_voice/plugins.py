"""Plugins: other local MCP servers whose tools the voice may call through the bridge.

A plugin is named in the settings file's ``[plugins.<name>]`` section (its command, folder
and the tools allowed to the voice), and runs only once the owner has approved it by typing
its name (``claude-voice plugins approve <name>``). The approval is fingerprints: one of
the command, folder and tools, checked before the plugin is started, and ones of what each
allowed tool takes, whether it only reads, and every word the voice reads from the plugin
(each tool's description and the plugin's own instructions), checked once it has said so.
A change to any of them needs approving again: typed, or by voice in developer mode when
only the tools changed (ADR-CV-010). Each plugin runs as its own long-lived child process,
started again with a growing pause when it fails; every call has a time limit. Only the
allowed tools are offered to the voice, named after the plugin, and the plugin's own
safety rules stay in the plugin.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import os
import threading
from collections.abc import AsyncIterator, Callable
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import invocation
from .config import PluginSpec

log = logging.getLogger("claude_voice")
APPROVALS = Path("~/.config/claude-voice/plugins-approved.json")
START_SECONDS = 15.0  # how long a plugin may take to start
# How long a plugin's first answer may take: after new code, uv may install packages first and
# heavy libraries load (the vacuum's imports alone take 5 to 10 s), so 15 s was too short.
LIST_SECONDS = 60.0
HOPS = 6  # the most calls one chain of `next` makes, whatever the plugin says (standard rule 4a)
HOP = "_hop"  # the bridge's count of calls in a chain, stamped into `next` and never forwarded
# The cap trusts the voice to pass HOP back; a voice that drops it starts again at 0. So the
# plugin's own record is the real bound on a chain, and this cap is only a backstop.
HOP_SCHEMA = {
    "type": "integer",
    "minimum": 0,
    "maximum": HOPS - 1,
    "description": "set by the bridge in a next; pass it back unchanged",
}


class NotReady(ConnectionError):
    """A plugin or machine that did not start or answer in time."""


def _reason(exc: BaseException, limit: float) -> str:
    """Why a listing failed, in words: a time-out carries no message of its own, and a task
    group hides the error that ended it."""
    while isinstance(exc, BaseExceptionGroup) and exc.exceptions:
        exc = exc.exceptions[0]
    said = f"no answer within {limit:g} s" if isinstance(exc, TimeoutError) else str(exc)
    # RULE: the log says why a plugin could not be listed
    return f"{said} ({type(exc).__name__})" if said else type(exc).__name__


def _hash(shape: Any) -> str:
    return hashlib.sha256(json.dumps(shape, sort_keys=True).encode()).hexdigest()


def _place(spec: PluginSpec, branch: bool = True) -> dict[str, Any]:
    """Where the plugin runs from. The branch is left out on main, so an approval made before
    the branch counted still matches a plugin on main."""
    shape: dict[str, Any] = {"command": spec.command, "args": spec.args, "cwd": spec.cwd}
    # RULE: a change of branch stops the plugin
    if branch and spec.branch != "main":
        shape["branch"] = spec.branch
    return shape


def fingerprint(spec: PluginSpec) -> str:
    """What the owner approved to run: the command, folder, branch and tools, never anything
    secret."""
    return _hash({**_place(spec), "tools": sorted(spec.tools)})


def place_fingerprint(spec: PluginSpec) -> str:
    """Where the plugin runs from: its command, folder and branch, without its tools."""
    return _hash(_place(spec))


def input_fingerprint(spec: PluginSpec, listing: dict[str, dict[str, Any]]) -> str:
    """What the owner approved each allowed tool to take, as the plugin described it."""
    return _hash({tool: listing.get(tool, {}).get("schema") for tool in sorted(spec.tools)})


def tool_fingerprints(spec: PluginSpec, listing: dict[str, dict[str, Any]]) -> dict[str, str]:
    """Each allowed tool's inputs, read-only mark and words for the voice, one fingerprint per
    tool, so a later change can be named tool by tool."""
    shown = {tool: listing.get(tool, {}) for tool in spec.tools}
    seen = ("schema", "read_only", "description", "rules")
    return {tool: _hash({k: t.get(k) for k in seen}) for tool, t in shown.items()}


def reads_fingerprint(spec: PluginSpec, listing: dict[str, dict[str, Any]]) -> str:
    """Every word the voice reads from the plugin: each allowed tool's description and the
    plugin's own instructions, which travel with it."""
    shown = {tool: listing.get(tool, {}) for tool in sorted(spec.tools)}
    return _hash({tool: [t.get("description"), t.get("rules")] for tool, t in shown.items()})


def mark_fingerprint(spec: PluginSpec, listing: dict[str, dict[str, Any]]) -> str:
    """Which allowed tools say they only read, as the plugin described them."""
    return _hash({tool: listing.get(tool, {}).get("read_only") for tool in sorted(spec.tools)})


def _approvals(path: Path) -> dict[str, Any]:
    try:
        return dict(json.loads(path.expanduser().read_text()))
    except (OSError, ValueError):
        return {}


def read_approvals(path: Path | None = None) -> dict[str, Any]:
    """Every plugin's approval as the file holds it now, read fresh."""
    return _approvals(path or APPROVALS)


def _approval(spec: PluginSpec, path: Path | None) -> dict[str, Any]:
    found = _approvals(path or APPROVALS).get(spec.name)
    return found if isinstance(found, dict) else {}  # an approval from before inputs counted is none


def _each(found: dict[str, Any]) -> dict[str, str] | None:
    """Each approved tool's fingerprint, or None for an approval from before tools counted one
    by one."""
    each = found.get("tools")
    return each if isinstance(each, dict) and each else None


def approved(spec: PluginSpec, path: Path | None = None) -> bool:
    """Whether the plugin runs: everything but its tools is as the owner approved it. An
    approval from before tools counted one by one needs its tools unchanged too."""
    found = _approval(spec, path)
    each = _each(found)
    if each is None:
        # RULE: a plugin runs only as the owner approved it
        return _approval(spec, path).get("runs") == fingerprint(spec)
    # RULE: a move together with a tools change still stops the plugin
    return found.get("runs") == _hash({**_place(spec), "tools": sorted(each)})


def ready_and_waiting(
    spec: PluginSpec, listing: dict[str, dict[str, Any]], path: Path | None = None
) -> tuple[list[str], dict[str, str]]:
    """The configured tools approved as they are now, and the others with their fingerprints:
    new ones, and ones whose inputs, words, mark or the plugin's rules changed."""
    each = _each(_approval(spec, path)) or {}
    now = tool_fingerprints(spec, listing)
    # RULE: a tool added in the settings waits while the others run
    ready = [tool for tool in spec.tools if each.get(tool) == now[tool]]
    return ready, {tool: now[tool] for tool in spec.tools if tool not in ready}


def waiting_news(plugin: Plugin) -> str:
    """The owner's sentence for a plugin's waiting tools."""
    count = len(plugin.waiting)
    tools = "tool" if count == 1 else "tools"
    return f"{plugin.spec.name} has {count} {tools} waiting for your approval: {', '.join(plugin.waiting)}"


def announce_waiting(plugin: Plugin, told: Path) -> str | None:
    """The sentence to announce when a tool waits that was not waiting as it is now at the last
    look, else None. What was told is kept in `told`, so a restart does not repeat it."""
    target = told.expanduser()
    body = _approvals(target)
    before = body.get(plugin.spec.name) or {}
    # RULE: a waiting tool is announced once per state
    fresh = [tool for tool, mark in plugin.waiting.items() if before.get(tool) != mark]
    if plugin.waiting != before:
        _save(target, {**body, plugin.spec.name: dict(plugin.waiting)})
    return waiting_news(plugin) if fresh else None


def waiting_items(running: list[Plugin]) -> list[dict[str, Any]]:
    """One item on the work page for each plugin with tools waiting for the owner."""
    return [
        {
            "id": f"plugin-waiting-{plugin.spec.name}",
            "text": waiting_news(plugin),
            "command": f"{invocation.command()} plugins approve {plugin.spec.name}",
            "since": plugin.waiting_since,
            "repo": None,
            "session": None,
            "prompt": None,
        }
        for plugin in running
        if plugin.waiting
    ]


def waiting_said(waiting: dict[str, str]) -> str:
    """In a few words, how a running plugin stands."""
    return f"running, with {len(waiting)} waiting: {', '.join(waiting)}" if waiting else "started"


def inputs_approved(spec: PluginSpec, listing: dict[str, dict[str, Any]], path: Path | None = None) -> bool:
    found = _approval(spec, path)
    # RULE: a plugin tool whose inputs changed is not offered until approved again
    if found.get("inputs") != input_fingerprint(spec, listing):
        return False
    # RULE: what the voice reads is covered by the approval
    if found.get("reads") != reads_fingerprint(spec, listing):
        return False  # an approval from before descriptions counted is approved once more
    # RULE: a changed read only mark needs approving again
    return found.get("marks") == mark_fingerprint(spec, listing)


def known_place(spec: PluginSpec, path: Path | None = None) -> bool:
    """Whether the owner once typed approval for this plugin's command and folder."""
    found = _approval(spec, path)
    # RULE: a new command or folder is never approved by voice
    return found.get("place") == place_fingerprint(spec) or found.get("runs") == fingerprint(spec)


def changes(spec: PluginSpec, listing: dict[str, dict[str, Any]], path: Path | None = None) -> list[str]:
    """Each tool that differs from what the owner approved, in a few words to read aloud.
    An approval from before tools were counted one by one names every tool."""
    stored = _approval(spec, path).get("tools")
    each: dict[str, str] = stored if isinstance(stored, dict) else {}
    now = tool_fingerprints(spec, listing)
    said = [_change(tool, listing.get(tool), new=bool(each) and tool not in each) for tool in spec.tools]
    said = [s for tool, s in zip(spec.tools, said, strict=True) if not each or each.get(tool) != now[tool]]
    return said + [f"{tool} is no longer offered" for tool in each if tool not in now]


def _change(tool: str, found: dict[str, Any] | None, new: bool) -> str:
    """One changed tool, in a few words."""
    if found is None:
        return f"{tool} is gone from the plugin"
    mark = ", only reads" if found.get("read_only") else ""
    return f"{'new: ' if new else ''}{tool} takes {takes(found.get('schema'))}{mark}"


def checkout_refusal(status: dict[str, Any] | None, branch: str) -> str | None:
    """Why the plugin's checkout may not be approved by voice, or None when it may: clean,
    on the branch it follows, and level with that branch as last fetched."""
    if status is None:
        return "its folder is not a git checkout"
    tracking = status.get("tracking") or {}
    # RULE: only a clean checkout level with its followed branch is approved by voice
    if status.get("dirty"):
        return "its checkout has changes that are not committed"
    if status.get("branch") != branch or not str(tracking.get("name", "")).endswith(f"/{branch}"):
        return f"its checkout is not on {branch}"
    if tracking.get("ahead") or tracking.get("behind"):
        return f"its checkout is not level with {tracking.get('name')}"
    return None


def takes(schema: Any) -> str:
    """What a tool takes, in a few words: its inputs' names, a ? on the optional ones."""
    properties = (schema or {}).get("properties") or {}
    needed = set((schema or {}).get("required") or [])
    return ", ".join(name if name in needed else f"{name}?" for name in properties) or "nothing"


def _record(spec: PluginSpec, listing: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return {
        "runs": fingerprint(spec),
        "inputs": input_fingerprint(spec, listing),
        "place": place_fingerprint(spec),
        "tools": tool_fingerprints(spec, listing),
        "marks": mark_fingerprint(spec, listing),
        "reads": reads_fingerprint(spec, listing),
    }


def _manifest_tools(spec: PluginSpec) -> dict[str, list[str]]:
    """The tools in the plugin's own manifest as it is at the approval, kept so a later change
    to the manifest adds only tools new since then; nothing for a plugin with no manifest."""
    from . import manifest

    try:
        found = manifest.read(Path(spec.cwd))
    except manifest.ManifestError:
        return {}
    return {"manifest_tools": list(found.tools)} if found.name == spec.name else {}


def approve(spec: PluginSpec, listing: dict[str, dict[str, Any]], path: Path | None = None) -> None:
    _write(spec.name, {**_record(spec, listing), **_manifest_tools(spec)}, path)
    log.info("plugin %s approved", spec.name)


def migrate_branches(specs: list[PluginSpec], path: Path | None = None) -> list[tuple[str, str]]:
    """Give each approval from before the branch counted the branch its settings name now, once,
    so no plugin stops at the upgrade. Only an approval whose place still matches gets one.
    Returns each plugin given a branch other than main, with that branch."""
    target = (path or APPROVALS).expanduser()
    body = _approvals(target)
    frozen: list[tuple[str, str]] = []
    changed = False
    for spec in specs:
        found = body.get(spec.name)
        # RULE: the branch is given once and a second start changes nothing
        if not isinstance(found, dict) or "migrated_branch" in found:
            continue
        before = _place(spec, branch=False)
        # RULE: an approval that did not match is not given a branch
        if found.get("place") != _hash(before):
            continue
        kept = sorted(_each(found) or spec.tools)  # the tools approved, not those configured now
        if found.get("runs") == _hash({**before, "tools": kept}):
            found["runs"] = _hash({**_place(spec), "tools": kept})
        found["place"] = place_fingerprint(spec)
        found["migrated_branch"] = {"value": spec.branch, "at": datetime.now(UTC).isoformat(timespec="seconds")}
        changed = True
        if spec.branch != "main":
            frozen.append((spec.name, spec.branch))
            log.info("plugin %s's approval now includes its branch, %s, from the settings", spec.name, spec.branch)
    if changed:
        _save(target, body)
    return sorted(frozen)


def frozen_branches_said(frozen: list[tuple[str, str]]) -> str | None:
    """One line for the owner naming every plugin whose approval was given a branch other than
    main, or None when there is none."""
    if not frozen:
        return None
    named = ", ".join(f"{name}: {branch}" for name, branch in frozen)
    return (
        f"These plugins' approvals now include the branch your settings name ({named}). "
        "Say take back and the plugin's name if one is wrong."
    )


def revoke(name: str, path: Path | None = None) -> bool:
    """Take a plugin's approval back, so a running bridge stops offering its tools; whether
    there was one."""
    target = (path or APPROVALS).expanduser()
    body = _approvals(target)
    if name not in body:
        return False
    # RULE: a revoked plugin is no longer approved
    del body[name]
    _save(target, body)
    log.info("plugin %s's approval was taken back", name)
    return True


def _write(name: str, entry: dict[str, Any], path: Path | None) -> None:
    target = (path or APPROVALS).expanduser()
    _save(target, {**_approvals(target), name: entry})


def _save(target: Path, body: dict[str, Any]) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    spare = target.with_name(target.name + ".tmp")
    spare.write_text(json.dumps(body, indent=2) + "\n")
    os.chmod(spare, 0o600)
    os.replace(spare, target)


def exposed_name(plugin: str, tool: str) -> str:
    """The name the voice sees: the plugin's name in front, once."""
    return tool if tool.startswith(f"{plugin}_") else f"{plugin}_{tool}"


def CLIENT(params: Any) -> Any:  # noqa: N802 - a stand-in for the MCP client class, replaced in tests
    """How the bridge talks to a plugin: the MCP library's own client."""
    from mcp.client import Client

    return Client(params)


def parameters(spec: PluginSpec) -> Any:
    """How to start the plugin. A plugin declared as `uv run <script>` is synced and started
    from its own environment (plugin_venv); refused there, it raises plugin_venv.Refused. Any
    other program is looked up the way the bridge finds its own tools (the service's PATH is
    short). The child gets the MCP library's small default environment: nothing of the
    bridge's own, so no key or token passes through it. This waits for the sync, so it is
    called off the event loop."""
    from mcp.client.stdio import StdioServerParameters

    from . import plugin_venv, procs

    if (started := plugin_venv.ready(spec)) is not None:
        return StdioServerParameters(command=started[0], args=started[1], cwd=spec.cwd)
    program = spec.command if os.path.isabs(spec.command) else procs.find(spec.command) or spec.command
    return StdioServerParameters(command=program, args=spec.args, cwd=spec.cwd)


class Plugin:
    """One plugin's child process: started on first use, kept, and started again after a failure."""

    start_seconds = START_SECONDS

    def __init__(self, spec: PluginSpec, client_factory: Callable[[Any], Any]) -> None:
        self.spec, self.client_factory = spec, client_factory
        self.state = "not started"
        self.offered: list[str] = []
        self.waiting: dict[str, str] = {}  # each tool waiting for the owner, with its fingerprint
        self.waiting_since: str | None = None  # when it was checked with tools waiting
        self.unlisted: str | None = None  # why the latest listing got no answer, or None
        self.refused: str | None = None  # why its start was refused, with what fixes it, or None
        self._failure: Exception | None = None
        self._down = False  # its latest start failed, and that was logged once
        self.last_failure: Exception | None = None  # what made the latest call fail, for a caller to tell apart
        self._client: Any = None
        self._lock = asyncio.Lock()
        self._runner: asyncio.Task[None] | None = None
        self._ready = asyncio.Event()
        self._closing = asyncio.Event()

    def list_seconds(self) -> float:
        """How long the first listing may wait for the plugin to answer."""
        # RULE: a first listing waits as long as the plugins own time limit
        return max(LIST_SECONDS, self.spec.timeout)

    def listing(self) -> dict[str, dict[str, Any]]:
        """The allowed tools as the plugin describes them, read once at start in a thread of
        its own, so the voice sees each tool's real description and arguments. When the
        plugin gave no answer, `unlisted` says why."""
        found: dict[str, dict[str, Any]] = {}
        limit = self.list_seconds()
        self.unlisted = self.refused = None

        async def read() -> None:
            async with self.open() as client:
                rules = getattr(client, "instructions", None) or None
                for tool in (await client.list_tools()).tools:
                    if tool.name in self.spec.tools:
                        marks = getattr(tool, "annotations", None)
                        found[tool.name] = {
                            "description": tool.description or "",
                            "schema": tool.input_schema,
                            "read_only": getattr(marks, "read_only_hint", None),
                            "rules": rules,  # the plugin's instructions, which travel with each tool
                        }

        def run() -> None:
            from .plugin_venv import Refused

            try:
                asyncio.run(asyncio.wait_for(read(), limit))
            except Refused as exc:
                self.refused = str(exc)
                self._unlisted(self.refused)
            except Exception as exc:
                self._unlisted(_reason(exc, limit))

        worker = threading.Thread(target=run, daemon=True)
        worker.start()
        worker.join(limit + 5)
        if worker.is_alive():  # still reading: what it found so far is not the whole answer
            self._unlisted(f"no answer within {limit + 5:g} s")
            return {}
        return found

    def _unlisted(self, why: str) -> None:
        self.unlisted, self.state = why, "failed to start"
        log.info("plugin %s could not be listed: %s", self.spec.name, why)

    async def _run(self) -> None:
        """Hold the connection open in a task of its own: the client must be closed in the
        task that opened it."""
        try:
            async with self.open() as client:
                self._client, self.state = client, "running"
                self._ready.set()
                log.info("plugin %s started%s", self.spec.name, " again" if self._down else "")
                self._down = False
                await self._closing.wait()
        except Exception as exc:
            self._failure = exc
            # RULE: a plugin that stays down is logged once not on every try
            if not self._down:
                why = _reason(exc, self.start_seconds)
                log.info(
                    "plugin %s ended: %s; tried again on each call, and logged again once it is back",
                    self.spec.name,
                    why,
                )
            self._down = True
        finally:
            self._client = None
            self._ready.set()  # wake a caller waiting for a start that failed

    @contextlib.asynccontextmanager
    async def open(self) -> AsyncIterator[Any]:
        """A connection to the plugin, to use with `async with`. How to start it is worked out
        on a thread, since a sync may take a while."""
        started = await asyncio.to_thread(parameters, self.spec)
        async with self.client_factory(started) as client:
            yield client

    def trouble(self, exc: Exception) -> str:
        """What to tell the voice when a call failed with `exc`."""
        return f"{self.spec.name} stopped working; it starts again on the next call"

    async def _connect(self) -> Any:
        if self._runner is None or self._runner.done():
            self._ready, self._closing = asyncio.Event(), asyncio.Event()
            self._runner = asyncio.create_task(self._run())
        try:
            await asyncio.wait_for(self._ready.wait(), self.start_seconds)
        except TimeoutError:
            raise NotReady(f"{self.spec.name} did not start in {self.start_seconds:g} s") from None
        if self._client is None:
            raise self._failure or NotReady(f"{self.spec.name} did not start")
        return self._client

    async def _drop(self) -> None:
        self._closing.set()
        runner = self._runner
        if runner is not None and self._client is None:
            runner.cancel()  # still connecting: nothing to close cleanly, so do not wait for it
        if runner is not None:
            with contextlib.suppress(Exception, asyncio.CancelledError):
                await asyncio.wait_for(asyncio.shield(runner), 5)

    async def stop(self) -> None:
        """Stop the process once a call already running has had its answer, waiting no
        longer than a call may take."""
        # RULE: a restart lets a call already running finish first
        held = False
        with contextlib.suppress(TimeoutError):
            held = await asyncio.wait_for(self._lock.acquire(), self.spec.timeout)
        try:
            await self._drop()
        finally:
            if held:
                self._lock.release()

    async def call(self, tool: str, arguments: dict[str, Any]) -> Any:
        """The plugin's answer, or RuntimeError saying what went wrong; a failed process is
        dropped, so the next call starts it again."""
        async with self._lock:
            try:
                client = await self._connect()
                # RULE: a plugin call that runs too long is cut off
                return await asyncio.wait_for(client.call_tool(tool, arguments), self.spec.timeout)
            except TimeoutError as exc:
                self.last_failure = exc
                await self._drop()
                self.state = "restarting"
                raise RuntimeError(f"{self.spec.name} took longer than {self.spec.timeout:g} s") from None
            except Exception as exc:
                self.last_failure = exc
                await self._drop()
                self.state = "restarting"
                if exc is not self._failure:  # a start that failed was logged when it ended
                    log.info("plugin %s failed: %s", self.spec.name, _reason(exc, self.spec.timeout))
                raise RuntimeError(self.trouble(exc)) from None

    def status(self) -> dict[str, Any]:
        return {
            "name": self.spec.name,
            "state": self.state,
            "tools": list(self.spec.tools),
            "waiting": list(self.waiting),
        }

    def shown(self, answer: Any) -> Any:
        """The answer as the voice is given it; a plugin's is passed on as it came."""
        return answer


def _as_object(text: str) -> dict[str, Any]:
    """A plugin's text answer as an object: its JSON when it is an object, else the text."""
    with contextlib.suppress(ValueError):
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    return {"text": text}


def _hops(arguments: dict[str, Any]) -> tuple[dict[str, Any], int | None]:
    """The arguments without the bridge's hop count, and that count: 0 when there is none,
    None when it is not a whole number below HOPS, which ends the chain. The count is only
    a backstop: a voice that drops it starts again at 0, so the plugin's own record bounds
    a chain."""
    rest = dict(arguments)
    # RULE: the plugin never sees the bridges hop count
    hop = rest.pop(HOP, 0)
    if isinstance(hop, bool) or not isinstance(hop, int) or not 0 <= hop < HOPS:
        return rest, None
    return rest, hop


def _with_hop(known: dict[str, Any]) -> dict[str, Any]:
    """The tool's schema as the voice is offered it: a read-only tool also takes the bridge's
    hop count, so a client that checks arguments never refuses the follow-up it was given."""
    schema = dict(known.get("schema") or {"type": "object", "properties": {}})
    # RULE: only a read-only tool offers the bridges hop count
    if known.get("read_only") is True:
        schema["properties"] = {**schema.get("properties", {}), HOP: HOP_SCHEMA}
    return schema


def _own_tool(named: Any, plugin: Plugin) -> str | None:
    """The plugin's own allowed tool that `named` names, as the plugin or the voice calls it."""
    own = {exposed_name(plugin.spec.name, t): t for t in plugin.spec.tools}
    return own.get(named) if isinstance(named, str) else None


def _refused_next(follow: Any, plugin: Plugin, listing: dict[str, dict[str, Any]], hop: int | None) -> str | None:
    """Why the bridge drops a `next` an answer carries, or None when it is passed on."""
    if hop is None:
        return "its hop count is not a small whole number"
    # RULE: the bridge stops a chain after six calls
    if hop + 1 >= HOPS:
        return f"a chain stops after {HOPS} calls"
    if not isinstance(follow, dict) or not isinstance(follow.get("args"), dict):
        return "it is not a tool and its args"
    tool = _own_tool(follow.get("tool"), plugin)
    # RULE: a next naming an unknown tool is refused
    if tool is None or tool not in listing:
        return f"{follow.get('tool')!r} is not one of {plugin.spec.name}'s tools"
    # RULE: a next naming a tool that presses is refused
    if listing[tool].get("read_only") is not True:
        return f"{tool} is not marked read-only"
    return None


def follow_up(result: Any, plugin: Plugin, listing: dict[str, dict[str, Any]], hop: int | None) -> Any:
    """The answer with its `next` passed on, named as the voice sees the tool and with the
    hop count stamped in, or without it when the bridge refuses it; rule 4a of the standard."""
    if not isinstance(result, dict) or "next" not in result:
        return result
    rest = {k: v for k, v in result.items() if k != "next"}
    follow = result["next"]
    why = _refused_next(follow, plugin, listing, hop)
    if why is not None:
        log.info("plugin %s: next dropped, since %s", plugin.spec.name, why)
        return rest
    tool = exposed_name(plugin.spec.name, follow["tool"])
    return {**rest, "next": {"tool": tool, "args": {**follow["args"], HOP: (hop or 0) + 1}}}


def offer(server: Any, plugin: Plugin, listing: dict[str, dict[str, Any]]) -> list[str]:
    """Offer the plugin's allowed tools on the bridge's MCP server; the names offered."""
    from mcp.server.mcpserver.exceptions import ToolError
    from mcp.server.mcpserver.tools.base import Tool

    class PluginTool(Tool):
        async def run(self, arguments: dict[str, Any], context: Any, convert_result: bool = False) -> Any:
            arguments, hop = _hops(arguments)
            try:
                answer = await plugin.call(self.meta["plugin_tool"], arguments)  # type: ignore[index]
            except RuntimeError as exc:
                raise ToolError(str(exc)) from None
            text = "\n".join(getattr(c, "text", "") for c in answer.content or [] if getattr(c, "text", None))
            if answer.is_error:
                raise ToolError(text or f"{plugin.spec.name} refused")
            result = answer.structured_content if answer.structured_content is not None else _as_object(text)
            result = follow_up(plugin.shown(result), plugin, listing, hop)
            return self.fn_metadata.convert_result(result) if convert_result else result

    async def placeholder() -> dict[str, Any]:  # never called: PluginTool.run answers instead
        return {}

    rules = instructions(listing)
    offered = []
    for tool in plugin.spec.tools:
        name = exposed_name(plugin.spec.name, tool)
        known = listing.get(tool, {})
        description = known.get("description") or f"{tool}, from the {plugin.spec.name} plugin"
        if rules:  # the plugin's own rules travel with every tool, whatever the client reads
            description = f"{description}\n\n{plugin.spec.name} rules: {rules}"
        base = Tool.from_function(placeholder, name=name, description=description, structured_output=True)
        fields = {k: getattr(base, k) for k in type(base).model_fields}
        made = PluginTool(**{**fields, "meta": {"plugin": plugin.spec.name, "plugin_tool": tool}})
        made.parameters = _with_hop(known)
        server._tool_manager._tools[name] = made
        offered.append(name)
    _instruct(server._lowlevel_server, plugin.spec.name, rules)
    return offered


def instructions(listing: dict[str, dict[str, Any]]) -> str | None:
    """The plugin's own instructions as its listing carries them, so the voice is told only
    the words the owner approved."""
    return next((str(t["rules"]) for t in listing.values() if t.get("rules")), None)


def _instruct(lowlevel: Any, name: str, rules: str | None) -> None:
    """Add the plugin's own rules to what the voice is told, once, however often it starts."""
    # RULE: a plugins own instructions reach the voice
    if rules and rules not in (lowlevel.instructions or ""):
        lowlevel.instructions = f"{lowlevel.instructions or ''}\n\nThe {name} plugin: {rules}".strip()


def withdraw(server: Any, plugin: Plugin) -> None:
    """Take the plugin's tools back from the voice."""
    for name in plugin.offered:
        server._tool_manager._tools.pop(name, None)
    plugin.offered = []


Checked = tuple[Plugin | None, dict[str, dict[str, Any]], str]


def check_one(spec: PluginSpec, approvals: Path | None = None, client: Callable[[Any], Any] | None = None) -> Checked:
    """List one plugin's tools and check them against the owner's approval, offering nothing
    yet: the plugin or None, its listing, and in a word why."""
    if not approved(spec, approvals):
        log.info("plugin %s is not approved; run: %s plugins approve %s", spec.name, invocation.command(), spec.name)
        return None, {}, "not approved"
    plugin = Plugin(spec, client or CLIENT)
    listing = plugin.listing()
    # RULE: a plugin that could not be listed is reported as not started never as changed tools
    if plugin.unlisted is not None:
        log.info("plugin %s did not start in time, so it is not offered: %s", spec.name, plugin.unlisted)
        # RULE: a plugin refused at start says why and what fixes it
        return None, {}, f"not started: {plugin.refused}" if plugin.refused else "not started"
    if _each(_approval(spec, approvals)) is None:  # an approval from before tools counted one by one
        if not inputs_approved(spec, listing, approvals):
            return _changed(spec, listing)
        return plugin, listing, "started"
    ready, waiting = ready_and_waiting(spec, listing, approvals)
    if not ready:
        return _changed(spec, listing)
    # A waiting tool is left out of the spec the plugin runs with, so no `next` can name it.
    plugin.spec, plugin.waiting = replace(spec, tools=ready), waiting
    if waiting:
        plugin.waiting_since = datetime.now(UTC).isoformat(timespec="seconds")
        log.info("plugin %s: %s wait for approval", spec.name, ", ".join(waiting))
    return plugin, {tool: listing[tool] for tool in ready if tool in listing}, waiting_said(waiting)


def _changed(spec: PluginSpec, listing: dict[str, dict[str, Any]]) -> Checked:
    log.info("plugin %s's tools changed; run: %s plugins approve %s", spec.name, invocation.command(), spec.name)
    return None, listing, "tools changed"


def present(server: Any, plugin: Plugin, listing: dict[str, dict[str, Any]], commit: str | None) -> None:
    """Offer a checked plugin's tools to the voice, and log at which commit it was accepted."""
    plugin.offered = offer(server, plugin, listing)
    log.info("plugin %s accepted again unchanged at %s", plugin.spec.name, commit or "an unknown commit")
    log.info("plugin %s offers %s", plugin.spec.name, ", ".join(plugin.offered))


def start_one(
    server: Any,
    spec: PluginSpec,
    approvals: Path | None = None,
    client: Callable[[Any], Any] | None = None,
    commit_of: Callable[[str], str | None] | None = None,
) -> tuple[Plugin | None, str]:
    """Start one plugin and offer its tools if it is as the owner approved it: the plugin,
    or None, and in a word why."""
    plugin, listing, why = check_one(spec, approvals, client)
    if plugin is not None:
        present(server, plugin, listing, commit_of(spec.cwd) if commit_of else None)
    return plugin, why


def start_approved(
    server: Any,
    specs: list[PluginSpec],
    approvals: Path | None = None,
    client: Callable[[Any], Any] | None = None,
    commit_of: Callable[[str], str | None] | None = None,
) -> list[Plugin]:
    """Start each plugin its owner approved and offer its tools on `server`; the ones offered."""
    started = []
    for spec in specs:
        plugin, _ = start_one(server, spec, approvals, client, commit_of)
        if plugin is not None:
            started.append(plugin)
    return started


def started_before_listening(behind: bool, start: Callable[[], list[Plugin]]) -> list[Plugin]:
    """The plugins `start` starts before the bridge listens: none when they start behind it."""
    # Ten plugins started one after another took over a minute under load, before the bridge
    # listened, so an update's check that the bridge answers gave up and rolled the update back.
    # RULE: a slow plugin never delays the bridge listening
    if behind:
        return []
    return start()
