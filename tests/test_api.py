"""The event API: what a companion app such as claude-voice-ui may read and change."""

import asyncio
import hashlib
import json
import sqlite3

import httpx
import pytest
from fakes import FakeClaude
from mcp.client import Client
from test_live import TRANSCRIPT, live, root  # noqa: F401  (fixtures)

from claude_voice.oauth import OAuthProvider
from claude_voice.server import build_app, build_server
from claude_voice.sessions import SessionManager
from claude_voice.store import Store

SECRET = "s3cret-token-for-the-event-api-tests"
HOST = "http://127.0.0.1:8811"
BEARER = {"Authorization": f"Bearer {SECRET}"}


class PRs:
    def __init__(self):
        self.asked = []

    def __call__(self, folder):
        self.asked.append(folder)
        return [{"number": 7, "title": "Fix the export", "url": "https://example.test/7", "draft": False}]


@pytest.fixture
def bridge(tmp_path, root, live):  # noqa: F811
    store = Store(tmp_path / "b.db")
    oauth = OAuthProvider(store, SECRET, HOST, static_token=SECRET)
    m = SessionManager(store, project_root=root, client_factory=FakeClaude())
    prs = PRs()
    server = build_server(
        m,
        oauth=oauth,
        conversations=lambda **k: [],
        live_dir=live,
        read_transcript=lambda sid, directory: TRANSCRIPT,
        list_prs=prs,
        ui_origins=["https://ui.example"],
    )
    return server, build_app(server, ui_origins=["https://ui.example"]), tmp_path / "b.db", prs


def client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=HOST)


async def test_nothing_is_answered_without_signing_in(bridge):
    _, app, _, _ = bridge
    async with client(app) as c:
        codes = [(await c.get(p)).status_code for p in ("/api/v1/sessions", "/api/v1/active", "/api/v1/events")]
        wrong = await c.get("/api/v1/sessions", headers={"Authorization": "Bearer nope"})
    assert codes == [401, 401, 401] and wrong.status_code == 401


async def test_a_bearer_token_reads_every_session_and_the_active_one(bridge):
    _, app, _, _ = bridge
    async with client(app) as c:
        out = (await c.get("/api/v1/sessions", headers=BEARER)).json()
    card = next(s for s in out["sessions"] if s["name"] == "billing-ab")
    assert card["project"] == "billing" and card["status"] == "idle" and out["active"] is None


async def test_signing_in_sets_a_cookie_scripts_cannot_read_and_only_its_hash_is_kept(bridge):
    _, app, db, _ = bridge
    async with client(app) as c:
        right = await c.post("/api/v1/signin", json={"secret": SECRET})
        listed = await c.get("/api/v1/sessions")
    assert right.status_code == 204 and listed.status_code == 200
    cookie = right.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie
    rows = sqlite3.connect(db).execute("SELECT token_hash FROM web_sessions").fetchall()
    assert rows == [(hashlib.sha256(right.cookies["cv_ui"].encode()).hexdigest(),)]


async def test_guessing_the_secret_on_the_page_stops_after_five_wrong_tries(bridge):
    _, app, _, _ = bridge
    async with client(app) as c:
        codes = [(await c.post("/api/v1/signin", json={"secret": "nope"})).status_code for _ in range(6)]
        after = await c.post("/api/v1/signin", json={"secret": SECRET})
    assert codes[:5] == [401] * 5 and codes[5] == 429 and after.status_code == 429


async def test_the_session_chosen_in_the_app_is_the_one_the_voice_talks_to(bridge):
    server, app, _, _ = bridge
    async with client(app) as c:
        put = await c.put("/api/v1/active", json={"session": "billing-ab"}, headers=BEARER)
        unknown = await c.put("/api/v1/active", json={"session": "nobody"}, headers=BEARER)
        active = (await c.get("/api/v1/active", headers=BEARER)).json()
    assert put.status_code == 200 and unknown.status_code == 404
    assert active["name"] == "billing-ab" and active["set_by"] == "page"
    async with Client(server) as mcp:
        seen = (await mcp.call_tool("get_active_session", {})).structured_content
        await mcp.call_tool("set_active_session", {"session": "billing-ab"})
    assert seen["name"] == "billing-ab"
    async with client(app) as c:
        assert (await c.get("/api/v1/active", headers=BEARER)).json()["set_by"] == "voice"


async def test_a_change_sent_from_another_site_is_refused(bridge):
    _, app, _, _ = bridge
    put = {"json": {"session": "billing-ab"}}
    async with client(app) as c:
        await c.post("/api/v1/signin", json={"secret": SECRET})
        foreign = await c.put("/api/v1/active", headers={"Origin": "https://evil.test"}, **put)
        allowed = await c.put("/api/v1/active", headers={"Origin": "https://ui.example"}, **put)
        bare = await c.put("/api/v1/active", **put)
    assert foreign.status_code == 403 and bare.status_code == 403 and allowed.status_code == 200


async def test_one_sessions_output_pending_questions_and_pull_requests(bridge):
    _, app, _, prs = bridge
    async with client(app) as c:
        output = (await c.get("/api/v1/sessions/billing-ab/output?limit=2", headers=BEARER)).json()
        pending = (await c.get("/api/v1/sessions/billing-ab/pending", headers=BEARER)).json()
        pulls = (await c.get("/api/v1/sessions/billing-ab/prs", headers=BEARER)).json()
        missing = await c.get("/api/v1/sessions/nobody/output", headers=BEARER)
    assert output["turns"][-1]["text"] == "All 12 tests pass." and "turns_left" in output
    assert pending == {"waiting": False, "question": None, "approvals": []}
    assert pulls["prs"][0]["number"] == 7 and prs.asked[-1].endswith("billing")
    assert missing.status_code == 404


def sse(body: str) -> list[tuple[str, dict]]:
    out = []
    for block in body.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines() if ": " in line and not line.startswith(":"))
        if "event" in lines:
            out.append((lines["event"], json.loads(lines["data"])))
    return out


async def test_the_event_stream_says_which_session_is_being_talked_to(bridge):
    server, app, _, _ = bridge
    async with client(app) as c:
        stream = asyncio.create_task(c.get("/api/v1/events?max_events=2", headers=BEARER))
        await asyncio.sleep(0.3)
        async with Client(server) as mcp:
            await mcp.call_tool("read_session_output", {"session": "billing-ab"})
            await mcp.call_tool("set_active_session", {"session": "billing-ab"})
        events = sse((await asyncio.wait_for(stream, 10)).text)
    assert events[0][0] == "focus" and events[0][1]["session"] == "billing-ab"
    assert events[0][1]["tool"] == "read_session_output"
    assert events[1][0] == "active" and events[1][1]["name"] == "billing-ab"


async def test_signing_out_ends_the_cookie(bridge):
    _, app, _, _ = bridge
    async with client(app) as c:
        await c.post("/api/v1/signin", json={"secret": SECRET})
        out = await c.post("/api/v1/signout", headers={"Origin": HOST})
        after = await c.get("/api/v1/sessions")
    assert out.status_code == 204 and after.status_code == 401


async def test_only_an_origin_named_in_the_settings_may_call_across_sites(bridge):
    _, app, _, _ = bridge
    ask = {"Access-Control-Request-Method": "GET"}
    async with client(app) as c:
        pre = await c.options("/api/v1/sessions", headers={"Origin": "https://ui.example", **ask})
        other = await c.options("/api/v1/sessions", headers={"Origin": "https://evil.test", **ask})
    assert pre.headers.get("access-control-allow-origin") == "https://ui.example"
    assert pre.headers.get("access-control-allow-credentials") == "true"
    assert "access-control-allow-origin" not in other.headers
