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


def behind_bridge(tmp_path, specs, slow, listed=list):
    """A bridge whose plugins start behind; each is approved first with a quick connection, then
    started through `slow`. `listed` makes the list of plugins the bridge reads."""
    approvals = tmp_path / "approved.json"
    for spec, quick in specs:
        plugins.approve(spec, plugins.Plugin(spec, quick).listing(), approvals)
    store = Store(tmp_path / "b.db")
    (tmp_path / "home").mkdir(exist_ok=True)
    m = SessionManager(store, project_root=tmp_path / "home", client_factory=FakeClaude())
    repos = config.Config(
        config.Repos(), config.Paths(None, None, None, None), None, plugins=listed(spec for spec, _ in specs)
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
    gate, started = threading.Event(), threading.Event()

    def slow(params):
        gate.wait(5)  # the plugin is held until the test lets it go, or else gives up waiting
        started.set()
        return Client(fake)

    server = behind_bridge(tmp_path, [(GARAGE, lambda p: Client(fake))], slow)
    # Proven by order, not by a clock: a bridge that waited for the plugin is built only once
    # the plugin has started, which the hold above lets happen only after its five seconds.
    async with Client(server) as c:
        answered = "health" in await tool_names(c) and not started.is_set()
        gate.set()
        came = await offered(c, "garage_status")
    assert answered  # the bridge answers while the plugin is still held
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


async def test_a_bridge_that_stops_stops_the_plugin_syncs_still_running(tmp_path, monkeypatch):
    from claude_voice import plugin_venv

    stops: list[bool] = []
    monkeypatch.setattr(plugin_venv, "stop_running", lambda: stops.append(True) or 1)
    fake, _ = stand_in()
    server = behind_bridge(tmp_path, [(GARAGE, lambda p: Client(fake))], lambda p: Client(fake))
    async with Client(server) as c:
        assert await offered(c, "garage_status")
        assert not stops  # nothing is stopped while the bridge runs
    assert stops  # and the syncs still running are stopped as it stops


async def test_a_plugin_that_cannot_be_offered_never_stops_the_others(tmp_path, monkeypatch, caplog):
    garage, _ = stand_in()
    tv = tv_stand_in()
    present = plugins.present

    def breaks_on_garage(server, plugin, listing, commit):
        if plugin.spec.name == "garage":
            raise RuntimeError("the tool table broke")
        present(server, plugin, listing, commit)

    monkeypatch.setattr(plugins, "present", breaks_on_garage)
    server = garage_and_tv(tmp_path, garage, tv)
    with caplog.at_level("INFO", logger="claude_voice"):
        async with Client(server) as c:
            came = await offered(c, "tv_status")
            health = (await c.call_tool("health", {})).structured_content
    assert came  # the plugin after the broken one is still offered
    states = {p["name"]: p for p in health["plugins"]}
    assert states["tv"]["state"] == "not started"  # offered; its process starts at the first call
    assert states["garage"]["state"] == "could not be offered"
    assert "garage could not be offered" in caplog.text and "the tool table broke" in caplog.text


async def test_a_plugin_that_could_not_be_offered_is_listed_as_not_answering(tmp_path, monkeypatch, caplog):
    garage, _ = stand_in()

    def breaks(server, plugin, listing, commit):
        raise RuntimeError("could not log in from /home/x/garage/.env")

    monkeypatch.setattr(plugins, "present", breaks)
    server = behind_bridge(tmp_path, [(GARAGE, lambda p: Client(garage))], lambda p: Client(garage))
    with caplog.at_level("INFO", logger="claude_voice"):
        async with Client(server) as c:
            deadline = time.monotonic() + 5
            while "garage could not be offered" not in caplog.text:
                assert time.monotonic() < deadline
                await asyncio.sleep(0.05)
            out = (await c.call_tool("plugins_status", {})).structured_content
    assert out["plugins"] == [{"name": "garage", "answered": False, "line": "could not be offered"}]
    assert out["say"] == "0 of 1 plugins answered. No answer from garage."


def garage_and_tv(tmp_path, garage, tv):
    """A bridge whose garage and tv plugins start behind it, in that order."""
    fakes = {GARAGE.cwd: garage, TV.cwd: tv}
    specs = [(GARAGE, lambda p: Client(garage)), (TV, lambda p: Client(tv))]
    return behind_bridge(tmp_path, specs, lambda params: Client(fakes[params.cwd]))


async def test_health_never_carries_the_text_of_a_plugin_error(tmp_path, monkeypatch, caplog):
    garage, _ = stand_in()
    secret = "sk-test-0123456789abcdef"

    def leaks(server, plugin, listing, commit):
        raise RuntimeError(f"could not log in with {secret} from /home/x/garage/.env")

    monkeypatch.setattr(plugins, "present", leaks)
    server = behind_bridge(tmp_path, [(GARAGE, lambda p: Client(garage))], lambda p: Client(garage))
    with caplog.at_level("INFO", logger="claude_voice"):
        async with Client(server) as c:
            deadline = time.monotonic() + 5
            while "garage could not be offered" not in caplog.text:
                assert time.monotonic() < deadline
                await asyncio.sleep(0.05)
            health = (await c.call_tool("health", {})).structured_content
    states = {p["name"]: p for p in health["plugins"]}
    assert states["garage"]["why"] == "RuntimeError; see the bridge log on the laptop"
    assert secret not in str(health) and "/home/x/garage" not in str(health)
    assert secret in caplog.text  # the full text stays in the log on the laptop


async def test_a_plugin_offered_after_it_failed_is_no_longer_listed_as_failed(tmp_path, monkeypatch, caplog):
    garage, _ = stand_in()
    present, broke = plugins.present, []

    def breaks_once(server, plugin, listing, commit):
        if not broke:
            broke.append(True)
            raise RuntimeError("the tool table broke")
        present(server, plugin, listing, commit)

    monkeypatch.setattr(plugins, "present", breaks_once)
    server = behind_bridge(tmp_path, [(GARAGE, lambda p: Client(garage))], lambda p: Client(garage))
    with caplog.at_level("INFO", logger="claude_voice"):
        async with Client(server) as c:
            deadline = time.monotonic() + 5
            while "garage could not be offered" not in caplog.text:
                assert time.monotonic() < deadline
                await asyncio.sleep(0.05)
            restarted = (await c.call_tool("plugin_restart", {"name": "garage"})).structured_content
            health = (await c.call_tool("health", {})).structured_content
    assert restarted["ok"]
    assert [(p["name"], p["state"]) for p in health["plugins"]] == [("garage", "not started")]


async def test_a_plugin_start_that_runs_again_never_lists_a_failure_twice(tmp_path, monkeypatch):
    from claude_voice import gitstatus

    garage, _ = stand_in()
    tv = tv_stand_in()
    present = plugins.present
    looked, release = threading.Event(), threading.Event()

    def breaks_on_garage(server, plugin, listing, commit):
        if plugin.spec.name == "garage":
            raise RuntimeError("the tool table broke")
        present(server, plugin, listing, commit)

    def first_tv_look_waits(folder):
        if folder == TV.cwd and not looked.is_set():
            looked.set()  # the first start has listed garage's failure, and waits here on tv
            release.wait(5)
        return None

    monkeypatch.setattr(plugins, "present", breaks_on_garage)
    monkeypatch.setattr(gitstatus, "read", first_tv_look_waits)
    server = garage_and_tv(tmp_path, garage, tv)
    async with Client(server):
        assert await asyncio.to_thread(looked.wait, 5)
    release.set()  # the first start was cancelled as its client went; the next one runs again
    async with Client(server) as c:
        assert await offered(c, "tv_status")
        health = (await c.call_tool("health", {})).structured_content
    assert [p["name"] for p in health["plugins"]].count("garage") == 1


async def test_a_check_ended_by_a_cancel_never_stops_the_others(tmp_path, monkeypatch, caplog):
    garage, _ = stand_in()
    tv = tv_stand_in()
    check_one = plugins.check_one

    def cancelled_on_garage(spec, *args):
        if spec.name == "garage":
            raise asyncio.CancelledError
        return check_one(spec, *args)

    monkeypatch.setattr(plugins, "check_one", cancelled_on_garage)
    server = garage_and_tv(tmp_path, garage, tv)
    with caplog.at_level("INFO", logger="claude_voice"):
        async with Client(server) as c:
            assert await offered(c, "tv_status")
            health = (await c.call_tool("health", {})).structured_content
    states = {p["name"]: p for p in health["plugins"]}
    assert states["garage"]["state"] == "could not be offered"


class Unreadable(list):
    """A list of plugins that breaks once `broken` is set, so the start fails as a whole."""

    broken = False

    def __iter__(self):
        if self.broken:
            raise RuntimeError("the plugin list broke")
        return super().__iter__()


async def test_a_plugin_start_that_fails_as_a_whole_is_logged(tmp_path, caplog):
    server = behind_bridge(tmp_path, [], lambda p: None, listed=Unreadable)
    with caplog.at_level("INFO", logger="claude_voice"):
        Unreadable.broken = True
        try:
            async with Client(server) as c:
                deadline = time.monotonic() + 5
                while "the plugins could not be started" not in caplog.text:
                    assert time.monotonic() < deadline
                    await asyncio.sleep(0.05)
                await c.list_tools()
        finally:
            Unreadable.broken = False
    assert "the plugin list broke" in caplog.text
