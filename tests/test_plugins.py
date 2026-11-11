"""Plugins: other local MCP servers whose approved tools the voice can call through the bridge."""

import asyncio
import json
import logging
import stat
import sys

import pytest
from fakes import FakeClaude
from mcp.client import Client
from mcp.server.mcpserver import MCPServer

from claude_voice import cli, config, plugins
from claude_voice.server import build_server
from claude_voice.sessions import SessionManager
from claude_voice.store import Store

GARAGE = config.PluginSpec(
    name="garage",
    command="garage-door-mcp",
    args=["--simulated"],
    cwd="/home/x/garage-door",
    tools=["garage_status", "garage_request"],
    timeout=2.0,
)


RULES = "Call garage_confirm only after the owner's own spoken yes."
LISTING = {"garage_status": {"description": "", "schema": {"type": "object", "properties": {}}}}


def stand_in(slow=False, phrase=False):
    """A stand-in for garage-door's MCP server, with one tool the voice must never get; with
    `phrase`, garage_request takes one input more than before."""
    server = MCPServer("garage", instructions=RULES)
    asked = []

    @server.tool()
    async def garage_status() -> dict:
        """Where the door is."""
        if slow:
            await asyncio.sleep(10)
        return {"door": "closed"}

    if phrase:

        @server.tool()
        async def garage_request(action: str, phrase: str = "") -> dict:
            """Ask to open or close the door, or press at once on a magic phrase."""
            asked.append(action)
            return {"token": f"t{len(asked)}", "action": action}

    else:

        @server.tool()
        async def garage_request(action: str) -> dict:
            """Ask to open or close the door."""
            asked.append(action)
            return {"token": f"t{len(asked)}", "action": action}

    @server.tool()
    async def garage_arm() -> dict:
        """Arm the alarm; never for the voice."""
        return {"armed": True}

    return server, asked


# -- the settings file and the owner's approval ------------------------------------------


def test_a_plugin_is_read_from_the_settings_file(tmp_path):
    f = tmp_path / "c.toml"
    f.write_text(
        '[plugins.garage]\ncommand = "garage-door-mcp"\nargs = ["--simulated"]\ncwd = "~/garage-door"\n'
        'tools = ["garage_status", "garage_request"]\n'
    )
    spec = config.load(f, env={"HOME": str(tmp_path)}).plugins[0]
    assert spec.name == "garage" and spec.cwd == str(tmp_path / "garage-door")
    assert spec.tools == ["garage_status", "garage_request"] and spec.timeout == 30.0
    assert config.load(tmp_path / "none.toml", env={"HOME": str(tmp_path)}).plugins == []


@pytest.mark.parametrize(
    "body", ['command = "x"\ncwd = "/t"\n', 'command = "x"\ncwd = "/t"\ntools = []\n', 'cwd = "/t"\ntools = ["a"]\n']
)
def test_a_plugin_without_a_command_or_any_allowed_tool_is_refused(tmp_path, body):
    f = tmp_path / "c.toml"
    f.write_text("[plugins.garage]\n" + body)
    with pytest.raises(config.ConfigError, match="garage"):
        config.load(f, env={"HOME": str(tmp_path)})


def test_a_plugin_runs_only_as_the_owner_approved_it(tmp_path):
    approvals = tmp_path / "approved.json"
    assert not plugins.approved(GARAGE, approvals)
    plugins.approve(GARAGE, LISTING, approvals)
    assert plugins.approved(GARAGE, approvals)
    if sys.platform != "win32":  # Windows has no owner-only file mode to set
        assert stat.S_IMODE(approvals.stat().st_mode) == 0o600
    widened = config.PluginSpec(**{**GARAGE.__dict__, "tools": [*GARAGE.tools, "garage_arm"]})
    moved = config.PluginSpec(**{**GARAGE.__dict__, "command": "other-program"})
    assert not plugins.approved(widened, approvals) and not plugins.approved(moved, approvals)


def test_the_owner_approves_a_plugin_by_typing_its_name(tmp_path, monkeypatch, capsys):
    f = tmp_path / "c.toml"
    f.write_text('[plugins.garage]\ncommand = "garage-door-mcp"\ncwd = "/g"\ntools = ["garage_status"]\n')
    approvals = tmp_path / "approved.json"
    env = {"HOME": str(tmp_path), "CLAUDE_VOICE_CONFIG": str(f)}
    monkeypatch.setattr(plugins, "APPROVALS", approvals)
    monkeypatch.setattr(plugins, "CLIENT", lambda params: Client(stand_in()[0]))
    monkeypatch.setattr("builtins.input", lambda prompt="": "no")
    assert cli.main(["plugins", "approve", "garage"], env=env) == 1
    monkeypatch.setattr("builtins.input", lambda prompt="": "garage")
    assert cli.main(["plugins", "approve", "garage"], env=env) == 0
    shown = capsys.readouterr().out
    spec = config.load(f, env=env).plugins[0]
    assert "garage-door-mcp" in shown and "garage_status" in shown and spec.cwd in shown
    assert "takes: nothing" in shown  # what each tool takes, as the plugin says now
    assert plugins.approved(spec, approvals)
    assert plugins.inputs_approved(spec, plugins.Plugin(spec, plugins.CLIENT).listing(), approvals)


def test_approving_with_nothing_typed_approves_nothing(tmp_path, monkeypatch, capsys):
    f = tmp_path / "c.toml"
    f.write_text('[plugins.garage]\ncommand = "garage-door-mcp"\ncwd = "/g"\ntools = ["garage_status"]\n')
    monkeypatch.setattr(plugins, "APPROVALS", tmp_path / "approved.json")

    def no_terminal(prompt=""):
        raise EOFError

    monkeypatch.setattr("builtins.input", no_terminal)
    env = {"HOME": str(tmp_path), "CLAUDE_VOICE_CONFIG": str(f)}
    assert cli.main(["plugins", "approve", "garage"], env=env) == 1
    assert "no answer was typed" in capsys.readouterr().out
    assert not (tmp_path / "approved.json").exists()


# -- through the bridge ------------------------------------------------------------------


def bridge(tmp_path, specs, approve=True, factory=None):
    approvals = tmp_path / "approved.json"
    for spec in specs if approve else []:
        plugins.approve(spec, plugins.Plugin(spec, factory).listing(), approvals)
    store = Store(tmp_path / "b.db")
    (tmp_path / "home").mkdir(exist_ok=True)
    m = SessionManager(store, project_root=tmp_path / "home", client_factory=FakeClaude())
    repos = config.Config(config.Repos(), config.Paths(None, None, None, None), None, plugins=specs)
    return build_server(
        m,
        conversations=lambda **k: [],
        live_dir=tmp_path / "live",
        repo_config=repos,
        plugin_approvals=approvals,
        plugin_client=factory,
    )


def bridge_with(tmp_path, machines, machine_folder, http, whois):
    """The bridge with other machines in its settings and nothing else."""
    store = Store(tmp_path / "b.db")
    (tmp_path / "home").mkdir(exist_ok=True)
    m = SessionManager(store, project_root=tmp_path / "home", client_factory=FakeClaude())
    repos = config.Config(config.Repos(), config.Paths(None, None, None, None), None, machines=machines)
    return build_server(
        m,
        conversations=lambda **k: [],
        live_dir=tmp_path / "live",
        repo_config=repos,
        machine_folder=machine_folder,
        machine_http=http,
        machine_whois=whois,
    )


async def tool_names(server):
    async with Client(server) as c:
        return {t.name for t in (await c.list_tools()).tools}


async def test_only_the_approved_tools_of_a_plugin_reach_the_voice(tmp_path):
    fake, asked = stand_in()
    server = bridge(tmp_path, [GARAGE], factory=lambda params: Client(fake))
    async with Client(server) as c:
        tools = {t.name: t for t in (await c.list_tools()).tools}
        out = (await c.call_tool("garage_request", {"action": "open"})).structured_content
        status = (await c.call_tool("garage_status", {})).structured_content
    assert "garage_arm" not in tools and "garage_status" in tools
    assert tools["garage_request"].input_schema["properties"]["action"]["type"] == "string"
    assert out == {"token": "t1", "action": "open"} and asked == ["open"] and status == {"door": "closed"}


async def test_a_plugin_the_owner_has_not_approved_adds_nothing(tmp_path):
    fake, _ = stand_in()
    without = await tool_names(bridge(tmp_path / "a", [], factory=lambda params: Client(fake)))
    unapproved = await tool_names(bridge(tmp_path / "b", [GARAGE], approve=False, factory=lambda p: Client(fake)))
    assert unapproved == without and not any(n.startswith("garage") for n in without)


async def test_a_tool_is_named_after_its_plugin():
    assert plugins.exposed_name("garage", "garage_status") == "garage_status"
    assert plugins.exposed_name("garage", "status") == "garage_status"


async def test_a_plugin_that_crashes_is_started_again(tmp_path):
    fake, _ = stand_in()
    starts = []

    class Breaks:
        def __init__(self, params):
            starts.append(params)
            self.inner = Client(fake)

        async def __aenter__(self):
            await self.inner.__aenter__()
            return self

        async def __aexit__(self, *exc):
            return await self.inner.__aexit__(*exc)

        async def list_tools(self):
            return await self.inner.list_tools()

        async def call_tool(self, name, arguments):
            if len(starts) <= 3:  # listed to approve and at start, then the first live process: it dies
                raise ConnectionError("the plugin went away")
            return await self.inner.call_tool(name, arguments)

    server = bridge(tmp_path, [GARAGE], factory=Breaks)
    async with Client(server) as c:
        first = await c.call_tool("garage_status", {})
        second = await c.call_tool("garage_status", {})
    assert first.is_error and "garage" in first.content[0].text
    assert not second.is_error and second.structured_content == {"door": "closed"}


async def test_a_plugin_call_that_runs_too_long_is_cut_off(tmp_path):
    fake, _ = stand_in(slow=True)
    server = bridge(tmp_path, [GARAGE], factory=lambda params: Client(fake))
    async with Client(server) as c:
        out = await asyncio.wait_for(c.call_tool("garage_status", {}), 8)
    assert out.is_error and "took longer than" in out.content[0].text


async def test_the_health_report_shows_each_plugin_and_its_tools(tmp_path):
    fake, _ = stand_in()
    server = bridge(tmp_path, [GARAGE], factory=lambda params: Client(fake))
    async with Client(server) as c:
        await c.call_tool("garage_status", {})
        health = (await c.call_tool("health", {})).structured_content
    garage = next(p for p in health["plugins"] if p["name"] == "garage")
    assert garage["state"] == "running" and garage["tools"] == ["garage_status", "garage_request"]


def test_the_approvals_file_holds_no_secret_only_a_fingerprint(tmp_path):
    approvals = tmp_path / "approved.json"
    plugins.approve(GARAGE, LISTING, approvals)
    body = json.loads(approvals.read_text())
    assert set(body) == {"garage"} and set(body["garage"]) == {"runs", "inputs", "place", "tools", "marks"}
    each = body["garage"].pop("tools")  # one fingerprint per tool, so a change can be named
    assert set(each) == set(GARAGE.tools)
    assert all(len(v) == 64 for v in [*body["garage"].values(), *each.values()])


async def test_a_plugins_own_instructions_reach_the_voice(tmp_path):
    """garage-door's rule that a confirmation needs the owner's own yes lives in its instructions."""
    fake, _ = stand_in()
    server = bridge(tmp_path, [GARAGE], factory=lambda params: Client(fake))
    async with Client(server) as c:
        tools = {t.name: t for t in (await c.list_tools()).tools}
        said = c.instructions or ""
    assert RULES in said
    assert RULES in tools["garage_request"].description and RULES in tools["garage_status"].description


async def test_a_plugin_tool_whose_inputs_changed_is_not_offered_until_approved_again(tmp_path):
    """garage_request learning a new input changes what one call can do, so the owner sees it first."""
    before, _ = stand_in()
    after, _ = stand_in(phrase=True)
    approvals = tmp_path / "approved.json"
    server = bridge(tmp_path, [GARAGE], factory=lambda params: Client(before))
    assert "garage_request" in await tool_names(server)
    changed = bridge(tmp_path, [GARAGE], approve=False, factory=lambda params: Client(after))
    assert not any(n.startswith("garage") for n in await tool_names(changed))
    plugins.approve(GARAGE, plugins.Plugin(GARAGE, lambda params: Client(after)).listing(), approvals)
    again = bridge(tmp_path, [GARAGE], approve=False, factory=lambda params: Client(after))
    assert "garage_request" in await tool_names(again)


# -- a plugin slow to start, or one that cannot be listed --------------------------------


class Unready:
    """A plugin that never answers its first listing, as a cold start that ran out of time
    looks to the bridge: a TimeoutError with nothing in its message."""

    def __init__(self, params=None):
        pass

    async def __aenter__(self):
        raise TimeoutError()

    async def __aexit__(self, *exc):
        return False


class Unhurried:
    """A plugin whose first start takes `seconds`, as uv and heavy imports do, then answers."""

    def __init__(self, server, seconds):
        self.inner, self.seconds = Client(server), seconds

    async def __aenter__(self):
        await asyncio.sleep(self.seconds)
        return await self.inner.__aenter__()

    async def __aexit__(self, *exc):
        return await self.inner.__aexit__(*exc)


def test_a_plugin_that_could_not_be_listed_is_reported_as_not_started_never_as_changed_tools(tmp_path, caplog):
    fake, _ = stand_in()
    approvals = tmp_path / "approved.json"
    plugins.approve(GARAGE, plugins.Plugin(GARAGE, lambda params: Client(fake)).listing(), approvals)
    before = approvals.read_text()
    with caplog.at_level(logging.INFO, logger="claude_voice"):
        plugin, why = plugins.start_one(MCPServer("bridge"), GARAGE, approvals, Unready)
    said = [r.getMessage() for r in caplog.records if r.name == "claude_voice"]
    assert plugin is None and why == "not started"
    assert not any("tools changed" in line or "plugins approve" in line for line in said)
    assert approvals.read_text() == before  # nothing to approve again


def test_the_log_says_why_a_plugin_could_not_be_listed(caplog):
    with caplog.at_level(logging.INFO, logger="claude_voice"):
        assert plugins.Plugin(GARAGE, Unready).listing() == {}
    line = next(r.getMessage() for r in caplog.records if "could not be listed" in r.getMessage())
    assert not line.endswith(": ") and "TimeoutError" in line


def test_a_first_listing_waits_as_long_as_the_plugins_own_time_limit(monkeypatch):
    """The vacuum's first start after new code took over 15 s; its calls may take 30."""
    monkeypatch.setattr(plugins, "START_SECONDS", 0.1)
    monkeypatch.setattr(plugins, "LIST_SECONDS", 0.1, raising=False)
    fake, _ = stand_in()
    patient = config.PluginSpec(**{**GARAGE.__dict__, "timeout": 3.0})
    listing = plugins.Plugin(patient, lambda params: Unhurried(fake, 0.5)).listing()
    assert set(listing) == {"garage_status", "garage_request"}
