"""The org chart in the bridge: what the phone page and the voice learn from it."""

import json
import subprocess
import sys
from pathlib import Path

import httpx
import pytest
from fakes import FakeClaude

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
