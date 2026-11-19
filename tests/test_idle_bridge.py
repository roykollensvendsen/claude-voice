"""Idle builders in the bridge: the manager's message, the voice's news, the chart's warnings."""

import asyncio
import json
import subprocess
import sys

import pytest
from fakes import FakeClaude
from mcp.client import Client

from claude_voice import idle
from claude_voice.server import build_server
from claude_voice.sessions import SessionManager
from claude_voice.store import Store

ORG = """
[programme]
code = "acme"
name = "Acme Robots"
backlog = "queue.md"
idle_report = true

[roles.manager]
name = "Manager"
sessions = ["acme-boss"]

[roles.lane]
name = "Lane"
sessions = ["lane-a"]
builder = true

[roles.arm-builder]
name = "Arm builder"
sessions = ["nobody-yet"]
"""
NOW = 1_000_000.0


class Clock:
    def __init__(self):
        self.now = NOW

    def __call__(self):
        return self.now


class Delivered:
    def __init__(self):
        self.sent = []

    async def __call__(self, name, text):
        self.sent.append((name, text))
        return {"delivered": True, "detail": ""}


@pytest.fixture
def pids():
    started = [subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"]) for _ in range(2)]
    yield [p.pid for p in started]
    for p in started:
        p.kill()
        p.wait()


@pytest.fixture
def bridge_with(tmp_path, pids):
    """A bridge whose courier is `deliver`, in a world of its own each time; and its clock."""
    made = []

    def make(deliver):
        here = tmp_path / str(len(made))
        made.append(here)
        root = here / "src"
        (root / "acme" / ".claude").mkdir(parents=True)
        (root / "acme" / ".claude" / "org.toml").write_text(ORG)
        state = here / "state"
        state.mkdir()
        (state / "queue.md").write_text("1. one\n2. two\n   blocked: no key\n")
        live = here / "live"
        live.mkdir()
        since = (NOW - 30 * 60) * 1000
        for pid, name in zip(pids, ("acme-boss", "lane-a"), strict=True):
            record = {"pid": pid, "sessionId": f"{name}-id", "cwd": str(root / "acme"), "name": name}
            (live / f"{pid}.json").write_text(json.dumps({**record, "status": "idle", "statusUpdatedAt": since}))
        clock = Clock()
        m = SessionManager(Store(here / "b.db"), project_root=root, client_factory=FakeClaude(), clock=clock)
        server = build_server(
            m,
            conversations=lambda **k: [],
            live_dir=live,
            read_transcript=lambda s, d: [],
            deliver=deliver,
            org_folders=[],
            idle_watch=idle.Watch(state, clock=clock, hour=lambda t: 12),
            manager_delivery_seconds=0.05,
        )
        return server, clock

    return make


@pytest.fixture
def bridge(bridge_with):
    deliver = Delivered()
    server, _ = bridge_with(deliver)
    return server, deliver


async def call(server, name, **args):
    async with Client(server) as c:
        return (await c.call_tool(name, args)).structured_content


async def test_the_manager_and_the_voice_hear_once_that_every_builder_is_idle(bridge):
    server, deliver = bridge
    cursor = (await call(server, "whats_new"))["cursor"]
    await asyncio.wait_for(server.idle_check(), 5)
    await asyncio.wait_for(server.idle_check(), 5)  # the same episode, a moment later: nothing more
    assert deliver.sent == [("acme-boss", "All builders in Acme Robots idle 15 min; 1 item waiting, 1 blocked.")]
    news = (await call(server, "whats_new", cursor=cursor))["events"]
    assert [(e["kind"], e["text"]) for e in news if e["kind"] == "idle_builders"] == [
        ("idle_builders", "Acme Robots: all builders idle 15 minutes with 1 item waiting, 1 blocked.")
    ]


class Stuck:
    """A courier that takes the message and never answers."""

    def __init__(self):
        self.sent = []

    async def __call__(self, name, text):
        self.sent.append((name, text))
        await asyncio.Event().wait()


class Answers:
    def __init__(self, out):
        self.out, self.sent = out, []

    async def __call__(self, name, text):
        self.sent.append((name, text))
        return self.out


async def let_deliveries_finish():
    for _ in range(5):
        await asyncio.sleep(0)


async def test_a_delivery_to_a_manager_never_holds_up_the_watch(bridge_with):
    stuck = Stuck()
    server, clock = bridge_with(stuck)
    await asyncio.wait_for(server.idle_check(), 5)
    await let_deliveries_finish()
    assert [name for name, _ in stuck.sent] == ["acme-boss"]


async def test_a_delivery_to_a_manager_gives_up_after_its_time(bridge_with):
    stuck = Stuck()
    server, clock = bridge_with(stuck)
    await asyncio.wait_for(server.idle_check(), 5)
    await asyncio.sleep(0.3)
    assert server.deliveries_in_flight == set()
    clock.now += 60
    await asyncio.wait_for(server.idle_check(), 5)
    await asyncio.sleep(0.3)
    assert len(stuck.sent) == 1  # it may have arrived, so it is never sent twice


async def test_a_message_the_courier_never_tried_is_sent_again_next_minute(bridge_with):
    courier = Answers({"delivered": False, "detail": "no courier", "attempted": False})
    server, clock = bridge_with(courier)
    await asyncio.wait_for(server.idle_check(), 5)
    await let_deliveries_finish()
    clock.now += 60
    await asyncio.wait_for(server.idle_check(), 5)
    await let_deliveries_finish()
    assert len(courier.sent) == 2


async def test_a_message_the_courier_may_have_sent_is_never_sent_twice(bridge_with):
    for out in ({"delivered": False, "detail": "half way", "attempted": True}, {"delivered": False, "detail": "?"}):
        courier = Answers(out)
        server, clock = bridge_with(courier)
        await asyncio.wait_for(server.idle_check(), 5)
        await let_deliveries_finish()
        clock.now += 60
        await asyncio.wait_for(server.idle_check(), 5)
        await let_deliveries_finish()
        assert len(courier.sent) == 1


async def test_the_org_chart_warns_about_a_builder_known_by_name_only(bridge):
    server, _ = bridge
    out = await call(server, "org_chart")
    assert "acme: role arm-builder is a builder by name only; mark it with builder = true" in out["warnings"]
