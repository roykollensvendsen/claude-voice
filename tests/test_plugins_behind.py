"""The bridge listens at once and starts its plugins behind, side by side: an update's check that
the bridge answers must never wait for ten plugins starting one after another."""

import asyncio
import threading
import time

from fakes import FakeClaude
from mcp.client import Client
from test_plugins import GARAGE, stand_in

from claude_voice import config, plugins
from claude_voice.server import build_server
from claude_voice.sessions import SessionManager
from claude_voice.store import Store

TV = config.PluginSpec(**{**GARAGE.__dict__, "name": "tv", "cwd": "/home/x/tv", "tools": ["tv_status"]})


def tv_stand_in():
    from mcp.server.mcpserver import MCPServer

    server = MCPServer("tv")

    @server.tool()
    async def tv_status() -> dict:
        """What the tv shows."""
        return {"say": "Off."}

    return server


def behind_bridge(tmp_path, specs, slow):
    """A bridge whose plugins start behind; each is approved first with a quick connection, then
    started through `slow`."""
    approvals = tmp_path / "approved.json"
    for spec, quick in specs:
        plugins.approve(spec, plugins.Plugin(spec, quick).listing(), approvals)
    store = Store(tmp_path / "b.db")
    (tmp_path / "home").mkdir(exist_ok=True)
    m = SessionManager(store, project_root=tmp_path / "home", client_factory=FakeClaude())
    repos = config.Config(
        config.Repos(), config.Paths(None, None, None, None), None, plugins=[spec for spec, _ in specs]
    )
    return build_server(
        m,
        conversations=lambda **k: [],
        live_dir=tmp_path / "live",
        repo_config=repos,
        plugin_approvals=approvals,
        plugin_client=slow,
        plugins_behind=True,
    )


async def tool_names(client):
    return {t.name for t in (await client.list_tools()).tools}


async def offered(client, name, seconds=10.0):
    """Whether `name` is offered within `seconds`."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if name in await tool_names(client):
            return True
        await asyncio.sleep(0.05)
    return False


async def test_a_slow_plugin_never_delays_the_bridge_listening(tmp_path):
    fake, _ = stand_in()
    gate = threading.Event()

    def slow(params):
        gate.wait(5)  # the plugin takes its time to start
        return Client(fake)

    began = time.monotonic()
    server = behind_bridge(tmp_path, [(GARAGE, lambda p: Client(fake))], slow)
    built = time.monotonic() - began
    async with Client(server) as c:
        answered = "health" in await tool_names(c)  # the bridge answers while the plugin starts
        gate.set()
        came = await offered(c, "garage_status")
    assert built < 2.0 and answered
    assert came  # and the plugin's tools follow once it has started


async def test_the_plugins_start_side_by_side(tmp_path):
    garage, _ = stand_in()
    tv = tv_stand_in()
    fakes = {GARAGE.cwd: garage, TV.cwd: tv}
    both = threading.Barrier(2, timeout=3)

    def side_by_side(params):
        both.wait()  # one after another, the first would wait here for the second in vain
        return Client(fakes[params.cwd])

    specs = [(GARAGE, lambda p: Client(garage)), (TV, lambda p: Client(tv))]
    server = behind_bridge(tmp_path, specs, side_by_side)
    async with Client(server) as c:
        assert await offered(c, "garage_status") and await offered(c, "tv_status")
