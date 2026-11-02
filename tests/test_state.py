"""One state per session, from a fixed set, with the time it last changed and an event when it does."""

import pytest
from fakes import FakeClaude
from mcp.client import Client
from test_fleet import asks, running, says, transcript, world  # noqa: F401  (fixtures)

from claude_voice import api
from claude_voice.server import build_server
from claude_voice.sessions import SessionManager
from claude_voice.store import Store


class Clock:
    def __init__(self, at=1_800_000_000.0):
        self.at = at

    def __call__(self):
        return self.at


def bridge(world, clock):  # noqa: F811
    store = Store(world / "b.db", clock=clock)
    m = SessionManager(store, project_root=world / "home", client_factory=FakeClaude(), clock=clock)
    return m, build_server(m, conversations=lambda **k: [], live_dir=world / "live", projects_dir=world / "projects")


def uses(tool):
    return {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "t1", "name": tool, "input": {}}]}}


async def states(server):
    async with Client(server) as c:
        rows = (await c.call_tool("fleet_recap", {})).structured_content["sessions"]
    return {r["name"]: r for r in rows}


@pytest.mark.parametrize(
    ("status", "entries", "state", "kind"),
    [
        ("busy", [says("on it")], "working", None),
        ("idle", [says("done")], "idle", None),
        ("shell", [says("running it")], "shell", None),
        ("waiting", [asks("Which file?", "a.py", "b.py")], "answer", "question"),
        ("waiting", [uses("Bash")], "approval", "approval"),
        ("waiting", [says("done")], "input", "input"),
    ],
)
async def test_every_running_session_is_in_one_named_state(world, status, entries, state, kind):  # noqa: F811
    running(world, "billing-ab", "billing", "s1", status=status)
    transcript(world, "billing", "s1", entries)
    _, server = bridge(world, Clock())
    row = (await states(server))["billing-ab"]
    assert row["state"] == state and row["waiting_kind"] == kind
    assert row["status"] == status  # the raw status stays as it was


@pytest.mark.parametrize(("stored", "state"), [("running", "working"), ("idle", "idle"), ("error", "stopped")])
async def test_a_bridge_session_is_named_in_the_same_states(world, stored, state):  # noqa: F811
    clock = Clock()
    m, server = bridge(world, clock)
    s = m.store.create_session(str(world / "home" / "billing"), label="fix")
    m.store.update_session(s["id"], status=stored)
    row = (await states(server))["fix"]
    assert row["state"] == state


async def test_the_time_in_a_state_counts_from_its_last_change_and_each_change_is_announced(world):  # noqa: F811
    clock = Clock()
    running(world, "billing-ab", "billing", "s1", status="busy")
    transcript(world, "billing", "s1", [says("on it")])
    _, server = bridge(world, clock)
    with api.HUB.subscribe() as events:
        first = (await states(server))["billing-ab"]
        clock.at += 300
        same = (await states(server))["billing-ab"]
        running(world, "billing-ab", "billing", "s1", status="idle")
        clock.at += 60
        changed = (await states(server))["billing-ab"]
        published = []
        while not events.empty():
            published.append(events.get_nowait())
    assert same["state_since"] == first["state_since"]
    assert changed["state"] == "idle" and changed["state_since"] != first["state_since"]
    status = [data for name, data in published if name == "status"]
    assert status[-1]["state"] == "idle" and status[-1]["previous"] == "working" and status[-1]["name"] == "billing-ab"


async def test_a_change_is_announced_while_someone_listens_even_if_nobody_asks(world):  # noqa: F811
    import asyncio

    running(world, "billing-ab", "billing", "s1", status="busy")
    transcript(world, "billing", "s1", [says("on it")])
    store = Store(world / "b.db")
    m = SessionManager(store, project_root=world / "home", client_factory=FakeClaude())
    server = build_server(
        m, conversations=lambda **k: [], live_dir=world / "live", projects_dir=world / "projects", watch_interval=0.05
    )
    with api.HUB.subscribe() as events:
        async with Client(server):  # the watcher runs while the server does
            await asyncio.sleep(0.3)
            running(world, "billing-ab", "billing", "s1", status="idle")
            name, data = await asyncio.wait_for(events.get(), 5)
            while name != "status":
                name, data = await asyncio.wait_for(events.get(), 5)
    assert data["state"] == "idle" and data["previous"] == "working"
