"""The Claude Code sessions on another machine, reached through its agent from the hub.

The other machine's bridge here is the real one, over a folder of running sessions made
for the test; the hub reaches it the way it reaches any approved machine.
"""

import json
import os

import pytest
from test_machines import Laptop, approve, call, hub

from claude_voice import agent
from claude_voice.config import ConfigError, load
from claude_voice.server import build_server
from claude_voice.sessions import SessionManager
from claude_voice.store import Store


class Recorder:
    def __init__(self):
        self.sent = []

    async def __call__(self, name, text):
        self.sent.append((name, text))
        return {"delivered": True}


def laptop_bridge(tmp_path):
    """The bridge on Lukas's laptop, with one session running there."""
    root, live = tmp_path / "home", tmp_path / "live"
    (root / "minecraft-mod").mkdir(parents=True)
    live.mkdir()
    record = {"pid": os.getpid(), "sessionId": "mod-id", "cwd": str(root / "minecraft-mod"), "name": "mod"}
    (live / f"{os.getpid()}.json").write_text(json.dumps({**record, "status": "idle", "kind": "interactive"}))
    deliver = Recorder()
    server = build_server(
        SessionManager(Store(tmp_path / "b.db"), project_root=root),
        conversations=lambda directory=None, limit=None: [],
        live_dir=live,
        deliver=deliver,
        read_transcript=lambda sid, directory: [],
    )
    return server, deliver


async def tool_names(server):
    from mcp.client import Client

    async with Client(server) as c:
        return {t.name for t in (await c.list_tools()).tools}


async def test_an_agent_offers_sessions_only_when_its_settings_say_so(tmp_path):
    pairing = agent.Pairing(tmp_path / "agent")
    without = agent.build_agent("hjemme", pairing)
    assert await tool_names(without) == {"agent_info"}
    bridge, _ = laptop_bridge(tmp_path)
    with_sessions = agent.build_agent("hjemme", pairing, bridge=bridge)
    assert "list_active_sessions" in await tool_names(with_sessions)


async def test_an_agent_offers_only_the_session_tools_on_its_list(tmp_path):
    bridge, _ = laptop_bridge(tmp_path)
    offered = await tool_names(agent.build_agent("hjemme", agent.Pairing(tmp_path / "agent"), bridge=bridge))
    assert offered == {*agent.SESSION_TOOLS, "agent_info"}
    assert not {"update_bridge", "health", "request_access", "hand_to_desktop"} & offered


async def test_the_hub_lists_and_messages_a_session_on_the_other_machine(tmp_path):
    bridge, deliver = laptop_bridge(tmp_path)
    laptop = Laptop(tmp_path, bridge=bridge)
    folder = tmp_path / "hub"
    async with laptop:
        await approve(laptop, folder)
        server, _ = hub(laptop, folder)
        listed = await call(server, "lukas-laptop_list_active_sessions")
        sent = await call(server, "lukas-laptop_message_active_session", session="mod", message="hei fra pappa")
    assert [s["name"] for s in listed.structured_content["sessions"]] == ["mod"]
    assert not sent.is_error and deliver.sent == [("mod", "hei fra pappa")]


def test_the_agent_settings_say_whether_sessions_are_offered(tmp_path):
    f = tmp_path / "config.toml"
    assert load(f, env={"HOME": str(tmp_path)}).agent_sessions is False
    f.write_text("[agent]\nsessions = true\n")
    assert load(f, env={"HOME": str(tmp_path)}).agent_sessions is True
    f.write_text('[agent]\nsessions = "yes"\n')
    with pytest.raises(ConfigError, match="true or false"):
        load(f, env={"HOME": str(tmp_path)})
