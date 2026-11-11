"""The org chart in the bridge: what the phone page and the voice learn from it."""

import json
import subprocess
import sys
from pathlib import Path

import httpx
import pytest
from fakes import FakeClaude
from mcp.client import Client

from claude_voice import api
from claude_voice.oauth import OAuthProvider
from claude_voice.server import build_app, build_server
from claude_voice.sessions import SessionManager
from claude_voice.store import Store

SECRET = "s3cret-token-for-the-org-chart-tests"
HOST = "http://127.0.0.1:8811"
BEARER = {"Authorization": f"Bearer {SECRET}"}
EXAMPLE = Path(__file__).parent.parent / "docs" / "examples" / "acme-robots" / ".claude" / "org.toml"
SHOP = (
    '[programme]\ncode = "shop"\nname = "Web shop"\norder = 0\n[roles.manager]\nname = "Manager"\nsessions = ["shop"]\n'
)


class Clock:
    def __init__(self):
        self.now = 1_000_000.0

    def __call__(self):
        return self.now


@pytest.fixture
def pids():
    """Processes that stay alive for the test, one per running session."""
    started = [subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"]) for _ in range(5)]
    yield [p.pid for p in started]
    for p in started:
        p.kill()
        p.wait()


@pytest.fixture
def world(tmp_path, pids):
    root = tmp_path / "src"
    for folder, text in (("acme-robots", EXAMPLE.read_text()), ("shop", SHOP)):
        (root / folder / ".claude").mkdir(parents=True)
        (root / folder / ".claude" / "org.toml").write_text(text)
    (root / "acme-robots" / ".claude" / "worktrees" / "w1").mkdir(parents=True)
    (root / "loose").mkdir()
    live = tmp_path / "live"
    live.mkdir()
    sessions = [
        ("acme-robots", "acme-id", "acme-robots"),
        ("fix-gripper", "gripper-id", "acme-robots/.claude/worktrees/w1"),
        ("shop", "shop-id", "shop"),
        ("loose", "loose-id", "loose"),
    ]
    for pid, (name, sid, folder) in zip(pids, sessions, strict=False):
        record = {"pid": pid, "sessionId": sid, "cwd": str(root / folder), "name": name, "status": "idle"}
        (live / f"{pid}.json").write_text(json.dumps({**record, "kind": "interactive", "updatedAt": 0}))
    return root, live


def bridge(tmp_path, world, clock=None):
    root, live = world
    store = Store(tmp_path / "b.db")
    oauth = OAuthProvider(store, SECRET, HOST, static_token=SECRET)
    m = SessionManager(store, project_root=root, client_factory=FakeClaude(), clock=clock or Clock())
    server = build_server(
        m,
        oauth=oauth,
        conversations=lambda **k: [],
        live_dir=live,
        read_transcript=lambda sid, directory: [],
        org_folders=[],
    )
    return server, build_app(server)


def client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=HOST)


async def listing(app):
    async with client(app) as c:
        r = await c.get("/api/v1/sessions", headers=BEARER)
    assert r.status_code == 200
    return r.json()


async def test_each_session_in_the_list_carries_its_place_in_the_org_chart(tmp_path, world):
    _, app = bridge(tmp_path, world)
    rows = {s["name"]: s for s in (await listing(app))["sessions"]}
    assert {k: rows["acme-robots"][k] for k in ("programme", "component", "role", "programme_wide", "address")} == {
        "programme": {"id": "acme", "name": "Acme Robots"},
        "component": None,
        "role": {"id": "manager", "name": "Manager"},
        "programme_wide": True,
        "address": "acme/manager",
    }
    assert rows["fix-gripper"]["component"] == {"id": "arm", "name": "Robot arm"}
    assert rows["fix-gripper"]["role"] is None and rows["fix-gripper"]["address"] == "acme/arm"
    assert rows["loose"]["programme"] is None and rows["loose"]["address"] is None
    assert rows["loose"]["machine"] is None  # this computer, as before


async def test_the_session_list_names_the_programmes_in_order(tmp_path, world):
    _, app = bridge(tmp_path, world)
    assert (await listing(app))["programmes"] == [
        {"id": "shop", "name": "Web shop", "order": 0},
        {"id": "acme", "name": "Acme Robots", "order": 1},
    ]


async def test_a_change_to_the_programmes_is_told_to_open_streams(tmp_path, world):
    clock = Clock()
    server, _ = bridge(tmp_path, world, clock)
    root, _ = world
    with api.HUB.subscribe() as events:
        server.org_check()
        (root / "shop" / ".claude" / "org.toml").write_text(SHOP.replace("Web shop", "The shop"))
        server.org_check()  # still the chart read a moment ago
        clock.now += 11
        server.org_check()
        told = [events.get_nowait() for _ in range(events.qsize())]
    assert [e for e in told if e[0] == "programmes"] == [
        (
            "programmes",
            {
                "programmes": [
                    {"id": "shop", "name": "The shop", "order": 0},
                    {"id": "acme", "name": "Acme Robots", "order": 1},
                ]
            },
        )
    ]


# -- reaching a session by its address, and the voice's overview ------------------------


class Delivered:
    def __init__(self):
        self.sent = []

    async def __call__(self, name, text):
        self.sent.append((name, text))
        return {"delivered": True, "detail": ""}


def voice(tmp_path, world):
    root, live = world
    deliver = Delivered()
    m = SessionManager(Store(tmp_path / "v.db"), project_root=root, client_factory=FakeClaude(), clock=Clock())
    server = build_server(
        m, conversations=lambda **k: [], live_dir=live, read_transcript=lambda s, d: [], deliver=deliver, org_folders=[]
    )
    return server, deliver


async def call(server, name, **args):
    async with Client(server) as c:
        return await c.call_tool(name, args)


async def test_a_message_reaches_a_session_by_its_address(tmp_path, world):
    server, deliver = voice(tmp_path, world)
    res = await call(server, "message_active_session", session="acme/manager", message="hello")
    assert not res.is_error, res.content
    assert deliver.sent == [("acme-robots", "hello")]


async def test_the_end_of_an_address_reaches_its_session_when_it_names_only_one(tmp_path, world):
    server, deliver = voice(tmp_path, world)
    res = await call(server, "message_active_session", session="Robot arm", message="hi")
    assert not res.is_error, res.content
    assert deliver.sent == [("fix-gripper", "hi")]


async def test_words_that_fit_several_sessions_are_answered_with_the_choices_to_ask(tmp_path, world):
    server, deliver = voice(tmp_path, world)
    res = await call(server, "message_active_session", session="manager", message="hi")
    assert res.is_error and deliver.sent == []
    text = res.content[0].text
    assert "acme/manager (acme-robots)" in text and "shop/manager (shop)" in text and "Ask the owner" in text


async def test_a_session_is_renamed_and_chosen_by_its_address(tmp_path, world):
    server, _ = voice(tmp_path, world)
    renamed = await call(server, "rename_session", session="arm", new_name="gripper")
    assert not renamed.is_error, renamed.content
    assert renamed.structured_content["was"] == "fix-gripper"
    chosen = await call(server, "set_active_session", session="shop/manager")
    assert not chosen.is_error, chosen.content
    assert chosen.structured_content["name"] == "shop"


async def test_the_org_chart_tool_says_who_handles_what(tmp_path, world):
    server, _ = voice(tmp_path, world)
    res = await call(server, "org_chart")
    assert res.structured_content["lines"] == [
        "Web shop (shop): manager, shop, running.",
        "Acme Robots (acme): manager, acme-robots, running; send decisions, proposals.",
        "Acme Robots (acme): arm quality, arm-quality, not running; send quality, facts.",
        "Outside any programme: loose.",
    ]


async def test_the_page_reads_a_programme_s_roles_and_who_fills_them(tmp_path, world):
    _, app = bridge(tmp_path, world)
    async with client(app) as c:
        r = await c.get("/api/v1/org/acme", headers=BEARER)
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    out = r.json()
    assert (out["id"], out["name"], out["unfilled"]) == ("acme", "Acme Robots", ["quality"])
    manager, quality = out["roles"]
    assert manager["sessions"] == ["acme-id"] and manager["running"] is True
    assert manager["routing"] == ["decisions", "proposals"] and manager["programme_wide"] is True
    assert quality["sessions"] == [] and quality["running"] is False
    assert quality["limits"] == "Changes tests, never the arm's code."


async def test_the_page_lists_the_programmes_with_their_unfilled_roles(tmp_path, world):
    _, app = bridge(tmp_path, world)
    async with client(app) as c:
        r = await c.get("/api/v1/org", headers=BEARER)
    assert r.json() == {
        "programmes": [
            {"id": "shop", "name": "Web shop", "order": 0, "roles": 1, "unfilled": []},
            {"id": "acme", "name": "Acme Robots", "order": 1, "roles": 2, "unfilled": ["quality"]},
        ]
    }


async def test_the_org_chart_is_only_read_and_only_after_signing_in(tmp_path, world):
    _, app = bridge(tmp_path, world)
    async with client(app) as c:
        unknown = await c.get("/api/v1/org/nope", headers=BEARER)
        anonymous = await c.get("/api/v1/org/acme")
        changed = await c.put("/api/v1/org/acme", headers=BEARER, json={})
    assert unknown.status_code == 404 and anonymous.status_code == 401 and changed.status_code == 405


def test_the_voice_is_told_to_ask_the_org_chart_who_handles_what():
    from claude_voice.server import INSTRUCTIONS

    assert "org_chart" in INSTRUCTIONS
