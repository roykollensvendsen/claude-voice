"""Plugins: other local MCP servers whose approved tools the voice can call through the bridge."""

import asyncio
import json
import stat

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


def stand_in(slow=False):
    """A stand-in for garage-door's MCP server, with one tool the voice must never get."""
    server = MCPServer("garage")
    asked = []

    @server.tool()
    async def garage_status() -> dict:
        """Where the door is."""
        if slow:
            await asyncio.sleep(10)
        return {"door": "closed"}

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
    plugins.approve(GARAGE, approvals)
    assert plugins.approved(GARAGE, approvals)
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
    monkeypatch.setattr("builtins.input", lambda prompt="": "no")
    assert cli.main(["plugins", "approve", "garage"], env=env) == 1
    monkeypatch.setattr("builtins.input", lambda prompt="": "garage")
    assert cli.main(["plugins", "approve", "garage"], env=env) == 0
    shown = capsys.readouterr().out
    assert "garage-door-mcp" in shown and "garage_status" in shown and "/g" in shown
    spec = config.load(f, env=env).plugins[0]
    assert plugins.approved(spec, approvals)


# -- through the bridge ------------------------------------------------------------------


def bridge(tmp_path, specs, approve=True, factory=None):
    approvals = tmp_path / "approved.json"
    for spec in specs if approve else []:
        plugins.approve(spec, approvals)
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
            if len(starts) <= 2:  # the listing at start, then the first live process: it dies
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
    plugins.approve(GARAGE, approvals)
    body = json.loads(approvals.read_text())
    assert set(body) == {"garage"} and len(body["garage"]) == 64
