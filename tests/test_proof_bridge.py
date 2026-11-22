"""A bridge built for a test or a proof starts no plugins, whatever settings it is given or finds.

A proof starts a second bridge beside the owner's. If that bridge started the plugins its settings
list, they would run twice, with their secrets loaded, though no proof needs them (#447).
"""

import asyncio

import pytest
from fakes import FakeClaude
from mcp.client import Client
from test_plugins import GARAGE, stand_in

from claude_voice import config, plugins
from claude_voice.server import build_server
from claude_voice.sessions import SessionManager
from claude_voice.store import Store


def with_garage():
    return config.Config(config.Repos(), config.Paths(None, None, None, None), None, plugins=[GARAGE])


def proof_bridge(tmp_path, behind, repo_config, started):
    """A proof bridge with the garage plugin approved; `started` counts each plugin start."""
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
        proof=True,
    )


@pytest.mark.parametrize("behind", [False, True])
@pytest.mark.parametrize("given", [True, False])
async def test_a_proof_bridge_starts_no_plugins(tmp_path, monkeypatch, caplog, behind, given):
    # Whether given or read from the owner's settings file, the settings list an approved plugin.
    monkeypatch.setattr(config, "load", lambda *a, **k: with_garage())
    started: list[str] = []
    with caplog.at_level("INFO", logger="claude_voice"):
        server = proof_bridge(tmp_path, behind, with_garage() if given else None, started)
        async with Client(server) as c:
            await asyncio.sleep(0.3)  # time for a start behind the bridge, were there one
            names = {t.name for t in (await c.list_tools()).tools}
            health = (await c.call_tool("health", {})).structured_content
    assert "garage_status" not in names and not started
    assert health["plugins"] == []
    assert "a proof bridge starts no plugins" in caplog.text
