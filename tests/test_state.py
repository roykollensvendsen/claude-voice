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
        ("shell", [says("started it")], "idle", None),  # ready, with a command running in the background
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


async def test_a_permission_prompt_is_not_hidden_by_an_old_question(world):  # noqa: F811
    running(world, "billing-ab", "billing", "s1", status="waiting", waiting_for="permission prompt")
    transcript(world, "billing", "s1", [asks("Asked long ago?", "Ja", "Nei")])
    _, server = bridge(world, Clock())
    row = (await states(server))["billing-ab"]
    assert row["state"] == "approval" and row["waiting_kind"] == "approval"
    assert "question" not in row


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


def slow_looks(monkeypatch, seconds=0.5):
    """Make each of the watcher's looks take `seconds`, as the first one can on a busy runner,
    so a test cannot pass by luck. Returns an event set once the second look begins, which
    means the first has finished: each look builds the session tree once."""
    import threading
    import time

    from claude_voice.tree import SessionTree

    build = SessionTree.build
    looks: list[float] = []
    second_look = threading.Event()

    def slow(self):
        looks.append(time.monotonic())
        if len(looks) >= 2:
            second_look.set()
        time.sleep(seconds)
        return build(self)

    monkeypatch.setattr(SessionTree, "build", slow)
    return second_look


async def test_a_change_is_announced_while_someone_listens_even_if_nobody_asks(world, monkeypatch):  # noqa: F811
    import asyncio

    second_look = slow_looks(monkeypatch)
    running(world, "billing-ab", "billing", "s1", status="busy")
    transcript(world, "billing", "s1", [says("on it")])
    store = Store(world / "b.db")
    m = SessionManager(store, project_root=world / "home", client_factory=FakeClaude())
    server = build_server(
        m, conversations=lambda **k: [], live_dir=world / "live", projects_dir=world / "projects", watch_interval=0.05
    )
    with api.HUB.subscribe() as events:
        async with Client(server):  # the watcher runs while the server does
            # A change is only news once a look has noted the state before it.
            assert await asyncio.to_thread(second_look.wait, 30), "the watcher never looked twice"
            running(world, "billing-ab", "billing", "s1", status="idle")
            name, data = await asyncio.wait_for(events.get(), 5)
            while name != "status":
                name, data = await asyncio.wait_for(events.get(), 5)
    assert data["state"] == "idle" and data["previous"] == "working"


async def test_a_new_turn_is_announced_so_a_detail_view_need_not_poll(world, monkeypatch):  # noqa: F811
    import asyncio
    import json as _json

    from claude_agent_sdk import project_key_for_directory

    second_look = slow_looks(monkeypatch)

    running(world, "billing-ab", "billing", "s1", status="busy")
    transcript(world, "billing", "s1", [says("on it")])
    m = SessionManager(Store(world / "b.db"), project_root=world / "home", client_factory=FakeClaude())
    server = build_server(
        m, conversations=lambda **k: [], live_dir=world / "live", projects_dir=world / "projects", watch_interval=0.05
    )
    path = world / "projects" / project_key_for_directory(world / "home" / "billing") / "s1.jsonl"
    with api.HUB.subscribe() as events:
        async with Client(server):
            # A turn is only news once a look has seen the conversation's size before it.
            # Each look builds the tree once, so a second build means the first look is done.
            assert await asyncio.to_thread(second_look.wait, 30), "the watcher never looked twice"
            with path.open("a") as f:
                f.write(
                    _json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "done"}]}})
                    + "\n"
                )
            name, data = await asyncio.wait_for(events.get(), 5)
            # The first look announces the turn already there; the one written now is the news.
            while name != "output" or data["index"] != 1:
                name, data = await asyncio.wait_for(events.get(), 5)
    assert data["session"] == "s1" and data["role"] == "assistant" and data["index"] == 1


async def test_a_turn_written_during_the_first_look_is_still_announced(world, monkeypatch):  # noqa: F811
    """After a restart the page reads the open session again as soon as the stream is back,
    but the bridge's first look takes seconds on a busy machine. A turn written in between
    must still reach the page, not wait for the session's next turn."""
    import asyncio
    import json as _json
    import threading
    import time

    from claude_agent_sdk import project_key_for_directory

    from claude_voice.tree import SessionTree

    build = SessionTree.build
    first_look = threading.Event()

    def slow(self):
        first_look.set()
        time.sleep(0.5)
        return build(self)

    monkeypatch.setattr(SessionTree, "build", slow)
    running(world, "billing-ab", "billing", "s1", status="busy")
    transcript(world, "billing", "s1", [says("on it")])
    m = SessionManager(Store(world / "b.db"), project_root=world / "home", client_factory=FakeClaude())
    server = build_server(
        m, conversations=lambda **k: [], live_dir=world / "live", projects_dir=world / "projects", watch_interval=0.05
    )
    path = world / "projects" / project_key_for_directory(world / "home" / "billing") / "s1.jsonl"
    with api.HUB.subscribe() as events:
        async with Client(server):
            assert await asyncio.to_thread(first_look.wait, 30), "the watcher never looked"
            with path.open("a") as f:  # while the first look is still under way
                f.write(
                    _json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "done"}]}})
                    + "\n"
                )
            name, data = await asyncio.wait_for(events.get(), 10)
            while name != "output":
                name, data = await asyncio.wait_for(events.get(), 10)
    assert data["session"] == "s1" and data["index"] == 1


async def test_a_quiet_session_costs_one_look_at_its_size_even_on_the_first_look(world, monkeypatch):  # noqa: F811
    """A transcript last written before the bridge started holds nothing the page has not read,
    so the first look after a restart does not read it, nor announce it."""
    import asyncio
    import os
    import threading

    from claude_agent_sdk import project_key_for_directory

    from claude_voice.tree import SessionTree

    build = SessionTree.build
    looks: list[int] = []
    second_look = threading.Event()

    def counted(self):
        looks.append(1)
        if len(looks) >= 2:
            second_look.set()
        return build(self)

    monkeypatch.setattr(SessionTree, "build", counted)
    running(world, "billing-ab", "billing", "s1", status="busy")
    transcript(world, "billing", "s1", [says("on it")])
    path = world / "projects" / project_key_for_directory(world / "home" / "billing") / "s1.jsonl"
    os.utime(path, (path.stat().st_atime, path.stat().st_mtime - 3600))  # an hour before the bridge
    m = SessionManager(Store(world / "b.db"), project_root=world / "home", client_factory=FakeClaude())
    server = build_server(
        m, conversations=lambda **k: [], live_dir=world / "live", projects_dir=world / "projects", watch_interval=0.05
    )
    with api.HUB.subscribe() as events:
        async with Client(server):
            assert await asyncio.to_thread(second_look.wait, 30), "the watcher never looked twice"
    heard = []
    while not events.empty():
        heard.append(events.get_nowait()[0])
    assert "output" not in heard
