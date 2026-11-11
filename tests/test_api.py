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

from claude_voice import api
from claude_voice.oauth import OAuthProvider
from claude_voice.server import build_app, build_server
from claude_voice.sessions import SessionManager
from claude_voice.store import Store

SECRET = "s3cret-token-for-the-event-api-tests"
HOST = "http://127.0.0.1:8811"
BEARER = {"Authorization": f"Bearer {SECRET}"}


class Delivered:
    """Stands in for the courier: every message arrives."""

    async def __call__(self, name, text):
        return {"delivered": True, "detail": ""}


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
        deliver=Delivered(),
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
        if "event" in lines and lines["event"] != "hello":  # every connect starts with hello
            out.append((lines["event"], json.loads(lines["data"])))
    return out


async def test_the_event_stream_says_which_session_is_being_talked_to(bridge):
    server, app, _, _ = bridge
    async with client(app) as c:
        stream = asyncio.create_task(c.get("/api/v1/events?max_events=2", headers=BEARER))
        await asyncio.sleep(0.3)
        async with Client(server) as mcp:
            await mcp.call_tool("message_active_session", {"session": "billing-ab", "message": "hi"})
            await mcp.call_tool("set_active_session", {"session": "billing-ab"})
        events = sse((await asyncio.wait_for(stream, 10)).text)
    assert events[0][0] == "focus" and events[0][1]["session"] == "billing-ab"
    assert events[0][1]["tool"] == "message_active_session"
    assert events[1][0] == "active" and events[1][1]["name"] == "billing-ab"


@pytest.mark.parametrize("tool", ["read_session_output", "side_question", "digest_session", "search_session_history"])
async def test_only_talking_to_a_session_puts_it_in_focus_and_a_look_at_it_does_not(bridge, tool):
    server, app, _, _ = bridge
    args = {"session": "billing-ab"}
    if tool in ("side_question", "search_session_history"):
        args["question" if tool == "side_question" else "query"] = "tests"
    with api.HUB.subscribe() as events:
        async with Client(server) as mcp:
            await mcp.call_tool(tool, args)
        seen = []
        while not events.empty():
            seen.append(events.get_nowait())
    names = [name for name, _ in seen]
    assert "focus" not in names
    assert ("peek", "billing-ab", tool) in [(n, d.get("session"), d.get("tool")) for n, d in seen]


async def test_an_event_published_from_another_thread_reaches_a_waiting_stream_at_once():
    import threading
    import time

    hub = api.EventHub()
    with hub.subscribe() as events:

        def publish():
            time.sleep(0.05)  # so the stream is already asleep, waiting for the next event
            hub.publish("status", {"state": "idle"})

        threading.Thread(target=publish).start()
        asked = time.monotonic()
        event = await asyncio.wait_for(events.get(), 3)
        waited = time.monotonic() - asked
    assert event == ("status", {"state": "idle"})
    assert waited < 0.5


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


def from_address(app, address):
    """A client whose requests reach the bridge from `address`, as uvicorn reports it behind a proxy."""
    transport = httpx.ASGITransport(app=app, client=(address, 4321))
    return httpx.AsyncClient(transport=transport, base_url=HOST)


@pytest.mark.parametrize("address", ["160.79.106.128", "8.8.8.8", "2001:4860:4860::8888"])
async def test_the_event_api_answers_only_this_computer_and_the_tailnet(bridge, address):
    _, app, _, _ = bridge
    async with from_address(app, address) as c:
        api = await c.get("/api/v1/sessions", headers=BEARER)
        ui = await c.get("/ui/")
        mcp = await c.post("/mcp", json={})
    assert api.status_code == 403 and ui.status_code == 403
    assert mcp.status_code == 401  # the MCP endpoint stays on the internet, behind its own login


@pytest.mark.parametrize("address", ["127.0.0.1", "::1", "100.101.102.103", "fd7a:115c:a1e0::e501:5695"])
async def test_this_computer_and_tailnet_devices_reach_the_event_api(bridge, address):
    _, app, _, _ = bridge
    async with from_address(app, address) as c:
        api = await c.get("/api/v1/sessions", headers=BEARER)
    assert api.status_code == 200


@pytest.mark.parametrize(
    ("forwarded", "code"),
    [
        ("160.79.106.128", 403),  # a Funnel caller, as Tailscale forwards it
        ("100.64.1.1, 160.79.106.128", 403),  # a Funnel caller pretending to be on the tailnet
        ("100.101.102.103", 200),  # a tailnet device, through the tailnet-only serve port
        ("fd7a:115c:a1e0::5", 200),
    ],
)
async def test_behind_tailscale_the_address_tailscale_added_last_decides(bridge, forwarded, code):
    _, app, _, _ = bridge
    async with from_address(app, "127.0.0.1") as c:  # Tailscale connects from this computer
        api = await c.get("/api/v1/sessions", headers={**BEARER, "X-Forwarded-For": forwarded})
    assert api.status_code == code


@pytest.mark.parametrize(("proto", "secure"), [("http", False), ("https", True)])
async def test_the_sign_in_cookie_follows_the_scheme_the_request_came_in_on(tmp_path, root, live, proto, secure):  # noqa: F811
    store = Store(tmp_path / "b.db")
    oauth = OAuthProvider(store, SECRET, "https://bridge.example", static_token=SECRET)  # the public address is https
    m = SessionManager(store, project_root=root, client_factory=FakeClaude())
    app = build_app(build_server(m, oauth=oauth, conversations=lambda **k: [], live_dir=live))
    async with from_address(app, "127.0.0.1") as c:
        r = await c.post(
            "/api/v1/signin",
            json={"secret": SECRET},
            headers={"X-Forwarded-For": "100.101.102.103", "X-Forwarded-Proto": proto},
        )
    assert r.status_code == 204
    assert ("secure" in r.headers["set-cookie"].lower()) is secure


async def test_the_choice_can_be_cleared_so_the_voice_decides_again(bridge):
    server, app, _, _ = bridge
    async with client(app) as c:
        await c.put("/api/v1/active", json={"session": "billing-ab"}, headers=BEARER)
        stream = asyncio.create_task(c.get("/api/v1/events?max_events=1", headers=BEARER))
        await asyncio.sleep(0.3)
        cleared = await c.delete("/api/v1/active", headers=BEARER)
        events = sse((await asyncio.wait_for(stream, 10)).text)
        after = (await c.get("/api/v1/active", headers=BEARER)).json()
        await c.post("/api/v1/signin", json={"secret": SECRET})
        foreign = await c.delete("/api/v1/active", headers={"Origin": "https://evil.test"})
    assert cleared.status_code == 204 and after is None
    assert events == [("active", None)]
    assert foreign.status_code == 403
    async with Client(server) as mcp:
        seen = (await mcp.call_tool("get_active_session", {})).structured_content
    assert seen["name"] is None


async def test_every_change_of_the_chosen_session_is_logged_with_who_made_it(bridge, caplog):
    import logging

    server, app, _, _ = bridge
    with caplog.at_level(logging.INFO, logger="claude_voice"):
        async with client(app) as c:
            await c.put("/api/v1/active", json={"session": "billing-ab"}, headers=BEARER)
            await c.delete("/api/v1/active", headers=BEARER)
        async with Client(server) as mcp:
            await mcp.call_tool("set_active_session", {"session": "billing-ab"})
    lines = [r.getMessage() for r in caplog.records if r.name == "claude_voice" and r.getMessage().startswith("active")]
    assert lines == ["active -> billing-ab by page", "active -> none by page", "active -> billing-ab by voice"]


async def test_the_plain_address_sends_a_browser_to_the_phone_page(bridge, tmp_path):
    server, _, _, _ = bridge
    page = tmp_path / "ui"
    page.mkdir()
    (page / "index.html").write_text("<html>page</html>")
    async with client(build_app(server, ui_dir=page)) as c:
        r = await c.get("/", follow_redirects=False)
    assert r.status_code in (302, 307) and r.headers["location"] == "/ui/"
