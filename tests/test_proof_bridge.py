"""A bridge built for a test or a proof starts no plugins, whatever settings it is given or finds.

A proof starts a second bridge beside the owner's. If that bridge started the plugins its settings
list, they would run twice, with their secrets loaded, though no proof needs them (#447).
"""

import asyncio
import time

import pytest
from fakes import FakeClaude
from mcp.client import Client
from test_plugins import GARAGE, stand_in

from claude_voice import config, plugins, server
from claude_voice.server import build_server, load_config, serve_forever
from claude_voice.sessions import SessionManager
from claude_voice.store import Store


def with_garage():
    return config.Config(config.Repos(), config.Paths(None, None, None, None), None, plugins=[GARAGE])


def bridge(tmp_path, behind, repo_config, started, proof=True):
    """A bridge with the garage plugin approved; `started` counts each plugin start."""
    fake, _ = stand_in()
    approvals = tmp_path / "approved.json"
    plugins.approve(GARAGE, plugins.Plugin(GARAGE, lambda p: Client(fake)).listing(), approvals)

    def client(params):
        started.append(params.cwd)
        return Client(fake)

    (tmp_path / "home").mkdir(exist_ok=True)
    manager = SessionManager(Store(tmp_path / "b.db"), project_root=tmp_path / "home", client_factory=FakeClaude())
    return build_server(
        manager,
        conversations=lambda **k: [],
        live_dir=tmp_path / "live",
        repo_config=repo_config,
        plugin_approvals=approvals,
        plugin_client=client,
        plugins_behind=behind,
        proof=proof,
    )


async def looked_at(server_, seconds=3.0):
    """The tool names and health of a bridge, once its plugins had `seconds` to start."""
    async with Client(server_) as c:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            names = {t.name for t in (await c.list_tools()).tools}
            if "garage_status" in names:
                break
            await asyncio.sleep(0.05)
        health = (await c.call_tool("health", {})).structured_content
        restarted = (await c.call_tool("plugin_restart", {"name": "garage"})).structured_content
    return names, health, restarted


@pytest.mark.parametrize("behind", [False, True])
@pytest.mark.parametrize("given", [True, False])
async def test_a_proof_bridge_starts_no_plugins(tmp_path, monkeypatch, caplog, behind, given):
    # Whether given or read from the owner's settings file, the settings list an approved plugin.
    monkeypatch.setattr(config, "load", lambda *a, **k: with_garage())
    started: list[str] = []
    with caplog.at_level("INFO", logger="claude_voice"):
        names, health, restarted = await looked_at(bridge(tmp_path, behind, with_garage() if given else None, started))
    assert "garage_status" not in names and not started
    assert health["plugins"] == [] and health["proof"] is True
    assert restarted["ok"] is False and not started  # nor does a restart by name start it
    assert "a proof bridge starts no plugins" in caplog.text


@pytest.mark.parametrize("behind", [False, True])
async def test_the_same_bridge_without_the_mark_starts_its_plugins_in_that_time(tmp_path, behind):
    """The control: the wait above is long enough for a plugin to start, so its absence means something."""
    started: list[str] = []
    names, health, _ = await looked_at(bridge(tmp_path, behind, with_garage(), started, proof=False))
    assert "garage_status" in names and started
    assert "proof" not in health


def settings(tmp_path, **extra):
    return {"CLAUDE_VOICE_ROOT": str(tmp_path), "CLAUDE_VOICE_DB": str(tmp_path / "b.db"), **extra}


@pytest.mark.parametrize("mark, proof", [("1", True), (" 1 ", True), ("", False), ("0", False), ("yes", False)])
def test_a_bridge_started_with_the_proof_mark_is_a_proof_bridge(tmp_path, mark, proof):
    assert load_config(settings(tmp_path, CLAUDE_VOICE_PROOF=mark), "stdio").proof is proof
    assert load_config(settings(tmp_path), "stdio").proof is False


@pytest.mark.parametrize("transport", ["stdio", "http"])
def test_a_bridge_served_with_the_proof_mark_is_built_as_a_proof_bridge(tmp_path, monkeypatch, transport):
    built: list[dict] = []

    class Stop(Exception):
        pass

    def builder(manager, **kwargs):
        built.append(kwargs)
        raise Stop  # nothing after the build runs, so nothing reaches the owner's state folder

    # Nothing may reach the owner's state folder: under systemd, as a session may run,
    # the restart record would otherwise write the bridge's own files there.
    monkeypatch.setattr(config, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(server.restarts, "restart_info", lambda *a, **k: {})
    monkeypatch.setattr(server, "build_server", builder)
    extra = {"CLAUDE_VOICE_TOKEN": "t" * 40} if transport == "http" else {}
    cfg = load_config(settings(tmp_path, CLAUDE_VOICE_PROOF="1", **extra), transport)
    with pytest.raises(Stop):
        serve_forever(cfg)
    assert len(built) == 1 and built[0]["proof"] is True
