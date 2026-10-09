"""Starting a background Claude Code session that the voice can talk to at once."""

import json
import os

import pytest
from fakes import FakeClaude
from mcp.client import Client

from claude_voice.server import build_server
from claude_voice.sessions import SessionManager
from claude_voice.store import Store

PID = os.getpid()


class Starter:
    """Stands in for `claude --bg`: records the call and registers the session like Claude Code does."""

    def __init__(self, live, register=True):
        self.live = live
        self.register = register
        self.calls = []

    async def __call__(self, cwd, name):
        self.calls.append((cwd, name))
        if self.register:
            (self.live / f"{name}.json").write_text(
                json.dumps(
                    {"pid": PID, "sessionId": f"sid-{name}", "cwd": cwd, "name": name, "kind": "bg", "status": "idle"}
                )
            )


@pytest.fixture
def world(tmp_path):
    root = tmp_path / "home"
    (root / "billing").mkdir(parents=True)
    live = tmp_path / "live"
    live.mkdir()
    return root, live


def bridge(tmp_path, world, starter):
    root, live = world
    m = SessionManager(Store(tmp_path / "b.db"), project_root=root, client_factory=FakeClaude())
    return build_server(m, conversations=lambda **k: [], live_dir=live, start_background=starter, start_seconds=1.0)


async def test_a_started_session_runs_in_the_background_and_is_listed_at_once(tmp_path, world):
    starter = Starter(world[1])
    async with Client(bridge(tmp_path, world, starter)) as c:
        out = (await c.call_tool("start_active_session", {"project": "billing", "name": "invoices"})).structured_content
        listed = (await c.call_tool("list_active_sessions", {})).structured_content
    assert out == {"name": "invoices", "claude_session_id": "sid-invoices", "project": "billing", "status": "idle"}
    assert starter.calls == [(str(world[0] / "billing"), "invoices")]
    assert "invoices" in {r["name"] for r in listed["sessions"]}


async def test_without_a_name_one_is_made_from_the_project_and_never_clashes(tmp_path, world):
    starter = Starter(world[1])
    async with Client(bridge(tmp_path, world, starter)) as c:
        first = (await c.call_tool("start_active_session", {"project": "billing"})).structured_content
        second = (
            await c.call_tool("start_active_session", {"project": "billing", "name": first["name"]})
        ).structured_content
    assert first["name"].startswith("billing-")
    assert second["name"] != first["name"] and second["name"].startswith(first["name"])


async def test_a_folder_outside_the_root_is_refused(tmp_path, world):
    async with Client(bridge(tmp_path, world, Starter(world[1]))) as c:
        bad = await c.call_tool("start_active_session", {"project": "/etc"})
    assert bad.is_error and "under" in bad.content[0].text


async def test_a_session_that_never_appears_is_reported_not_promised(tmp_path, world):
    async with Client(bridge(tmp_path, world, Starter(world[1], register=False))) as c:
        bad = await c.call_tool("start_active_session", {"project": "billing", "name": "ghost"})
    assert bad.is_error and "did not appear" in bad.content[0].text
