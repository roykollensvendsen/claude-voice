"""Idle builders in the bridge: the manager's message, the voice's news, the chart's warnings."""

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
def bridge(tmp_path, pids):
    root = tmp_path / "src"
    (root / "acme" / ".claude").mkdir(parents=True)
    (root / "acme" / ".claude" / "org.toml").write_text(ORG)
    state = tmp_path / "state"
    state.mkdir()
    (state / "queue.md").write_text("1. one\n2. two\n   blocked: no key\n")
    live = tmp_path / "live"
    live.mkdir()
    since = (NOW - 30 * 60) * 1000
    for pid, name in zip(pids, ("acme-boss", "lane-a"), strict=True):
        record = {"pid": pid, "sessionId": f"{name}-id", "cwd": str(root / "acme"), "name": name}
        (live / f"{pid}.json").write_text(json.dumps({**record, "status": "idle", "statusUpdatedAt": since}))
    clock = Clock()
    deliver = Delivered()
    m = SessionManager(Store(tmp_path / "b.db"), project_root=root, client_factory=FakeClaude(), clock=clock)
    server = build_server(
        m,
        conversations=lambda **k: [],
        live_dir=live,
        read_transcript=lambda s, d: [],
        deliver=deliver,
        org_folders=[],
        idle_watch=idle.Watch(state, clock=clock, hour=lambda t: 12),
    )
    return server, deliver


async def call(server, name, **args):
    async with Client(server) as c:
        return (await c.call_tool(name, args)).structured_content


async def test_the_manager_and_the_voice_hear_once_that_every_builder_is_idle(bridge):
    server, deliver = bridge
    cursor = (await call(server, "whats_new"))["cursor"]
    await server.idle_check()
    await server.idle_check()  # the same episode, a moment later: nothing more
    assert deliver.sent == [("acme-boss", "All builders in Acme Robots idle 15 min; 1 item waiting, 1 blocked.")]
    news = (await call(server, "whats_new", cursor=cursor))["events"]
    assert [(e["kind"], e["text"]) for e in news if e["kind"] == "idle_builders"] == [
        ("idle_builders", "Acme Robots: all builders idle 15 minutes with 1 item waiting, 1 blocked.")
    ]


async def test_the_org_chart_warns_about_a_builder_known_by_name_only(bridge):
    server, _ = bridge
    out = await call(server, "org_chart")
    assert "acme: role arm-builder is a builder by name only; mark it with builder = true" in out["warnings"]
