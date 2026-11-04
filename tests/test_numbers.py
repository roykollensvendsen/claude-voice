"""A short, stable number per session, so the owner can say "number 3" and see the same 3 on screen."""

import json
import os

import pytest
from fakes import FakeClaude
from mcp.client import Client

from claude_voice.server import build_server
from claude_voice.sessions import SessionManager
from claude_voice.store import Store

PID = os.getpid()


class Clock:
    def __init__(self):
        self.at = 1_800_000_000.0

    def __call__(self):
        return self.at


@pytest.fixture
def world(tmp_path):
    (tmp_path / "home" / "app").mkdir(parents=True)
    (tmp_path / "live").mkdir()
    return tmp_path


def running(world, name, sid):
    (world / "live" / f"{name}.json").write_text(
        json.dumps({"pid": PID, "sessionId": sid, "cwd": str(world / "home" / "app"), "name": name, "status": "idle"})
    )


def gone(world, name):
    (world / "live" / f"{name}.json").unlink()


def bridge(world, clock):
    store = Store(world / "b.db", clock=clock)
    m = SessionManager(store, project_root=world / "home", client_factory=FakeClaude(), clock=clock)
    return build_server(m, conversations=lambda **k: [], live_dir=world / "live", projects_dir=world / "projects")


async def numbers(server):
    async with Client(server) as c:
        rows = (await c.call_tool("fleet_recap", {})).structured_content["sessions"]
    return {r["name"]: r["number"] for r in rows}


async def test_each_session_gets_the_smallest_free_number_and_keeps_it(world):
    clock = Clock()
    running(world, "alpha", "s1")
    running(world, "beta", "s2")
    server = bridge(world, clock)
    first = await numbers(server)
    assert sorted(first.values()) == [1, 2]
    running(world, "gamma", "s3")
    second = await numbers(server)
    assert second["alpha"] == first["alpha"] and second["beta"] == first["beta"] and second["gamma"] == 3


async def test_a_new_session_gets_the_lowest_number_no_running_session_uses(world):
    """A number is free again as soon as its session ends, so the numbers stay small."""
    clock = Clock()
    running(world, "alpha", "s1")
    running(world, "beta", "s2")
    server = bridge(world, clock)
    before = await numbers(server)
    gone(world, "alpha")
    await numbers(server)
    running(world, "delta", "s4")
    after = await numbers(server)
    assert after["delta"] == before["alpha"] and after["beta"] == before["beta"]


async def test_the_numbers_survive_a_restart_of_the_bridge(world):
    clock = Clock()
    running(world, "alpha", "s1")
    running(world, "beta", "s2")
    before = await numbers(bridge(world, clock))
    assert await numbers(bridge(world, clock)) == before


@pytest.mark.parametrize("spoken", ["2", "nr 2", "nr. 2", "nummer 2", "number 2", "Number 2"])
async def test_a_session_can_be_named_by_its_number_wherever_a_tool_takes_a_session(world, spoken):
    clock = Clock()
    running(world, "alpha", "s1")
    running(world, "beta", "s2")
    server = bridge(world, clock)
    async with Client(server) as c:
        rows = (await c.call_tool("fleet_recap", {})).structured_content["sessions"]
        wanted = next(r for r in rows if r["number"] == 2)
        active = (await c.call_tool("set_active_session", {"session": spoken})).structured_content
        read = (await c.call_tool("read_session_output", {"session": spoken})).structured_content
    assert active["id"] == wanted["id"] and active["number"] == 2
    assert read["session"] == wanted["name"]
