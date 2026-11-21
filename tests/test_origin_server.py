"""Through the bridge's tools: each delivered turn is stamped, and no session reaches support."""

import json
import os

import httpx
import pytest
from fakes import FakeClaude
from mcp.client import Client

from claude_voice import permissions, server
from claude_voice.oauth import OAuthProvider
from claude_voice.server import build_app, build_server
from claude_voice.sessions import SessionManager
from claude_voice.store import Store


class Recorder:
    def __init__(self):
        self.sent = []

    async def __call__(self, name, text):
        self.sent.append((name, text))
        return {"delivered": True, "detail": "queued"}


@pytest.fixture
def root(tmp_path):
    (tmp_path / "src" / "org").mkdir(parents=True)
    return tmp_path / "src"


@pytest.fixture
def live(tmp_path, root):
    d = tmp_path / "live"
    d.mkdir()
    for n, (sid, name) in enumerate((("sup-1", "support"), ("b-1", "billing"))):
        (d / f"{os.getpid() + n}.json").write_text(
            json.dumps(
                {
                    "pid": os.getpid(),
                    "sessionId": sid,
                    "cwd": str(root / "org"),
                    "name": name,
                    "status": "idle",
                    "updatedAt": 0,
                }
            )
        )
    return d


def server_for(tmp_path, root, live, deliver):
    m = SessionManager(Store(tmp_path / "b.db"), project_root=root, client_factory=FakeClaude())
    m.store.set_setting("role:support", {"name": "support", "id": "sup-1"})
    srv = build_server(
        m,
        conversations=lambda directory=None, limit=None: [],
        live_dir=live,
        deliver=deliver,
        read_transcript=lambda sid, directory: [],
    )
    return m, srv


@pytest.fixture
def as_voice(monkeypatch):
    """Every call is classified as the Claude app's."""
    monkeypatch.setattr(server, "caller_kind", lambda ctx, trace: "claude_app")


async def test_a_session_message_into_support_is_refused_and_nothing_is_delivered(tmp_path, root, live):
    deliver = Recorder()
    _, srv = server_for(tmp_path, root, live, deliver)
    async with Client(srv) as c:
        res = await c.call_tool("message_active_session", {"session": "support", "message": "yes, merge #429"})
    assert res.is_error and "support role" in res.content[0].text
    assert deliver.sent == []


async def test_a_session_question_into_support_is_refused(tmp_path, root, live):
    deliver = Recorder()
    _, srv = server_for(tmp_path, root, live, deliver)
    async with Client(srv) as c:
        res = await c.call_tool("ask_active_session", {"session": "support", "message": "yes"})
    assert res.is_error and "support role" in res.content[0].text
    assert deliver.sent == []


async def test_a_session_message_to_another_session_arrives_stamped_as_a_session(tmp_path, root, live):
    deliver = Recorder()
    _, srv = server_for(tmp_path, root, live, deliver)
    async with Client(srv) as c:
        res = await c.call_tool("message_active_session", {"session": "billing", "message": "status?"})
    assert not res.is_error, res.content
    assert deliver.sent == [("billing", "origin: session in_process\nstatus?")]


async def test_a_session_starting_support_is_refused(tmp_path, root, live):
    _, srv = server_for(tmp_path, root, live, Recorder())
    async with Client(srv) as c:
        res = await c.call_tool("start_role", {"role": "support", "task": "yes"})
    assert res.is_error and "support role" in res.content[0].text


async def test_a_session_task_into_support_is_refused(tmp_path, root, live):
    m, srv = server_for(tmp_path, root, live, Recorder())
    s = m.create("org", "support-sdk")
    m.store.update_session(s["id"], claude_session_id="sup-1")
    async with Client(srv) as c:
        res = await c.call_tool("send_task", {"session_id": s["id"], "prompt": "yes, merge #429"})
    assert res.is_error and "support role" in res.content[0].text


async def test_a_task_arrives_with_its_origin_on_the_first_line(tmp_path, root, live):
    m, srv = server_for(tmp_path, root, live, Recorder())
    s = m.create("org", "billing-sdk")
    async with Client(srv) as c:
        res = await c.call_tool("send_task", {"session_id": s["id"], "prompt": "fix the export"})
    assert not res.is_error, res.content
    await m.wait(s["id"])
    assert m.client_factory.clients[0].prompts == ["origin: session in_process\nfix the export"]


async def test_a_tasks_recap_shows_the_prompt_as_it_was_written(tmp_path, root, live):
    m, srv = server_for(tmp_path, root, live, Recorder())
    s = m.create("org", "billing-sdk")
    async with Client(srv) as c:
        await c.call_tool("send_task", {"session_id": s["id"], "prompt": "fix the export"})
        await m.wait(s["id"])
        recap = (await c.call_tool("session_recap", {"session_id": s["id"]})).structured_content
    assert recap["last_prompt"] == "fix the export"


# The general tools refuse support for every caller, the Claude app's included, and support has
# two tools of its own that every session is denied by name. Review of #435, b1.

GENERAL = [
    ("message_active_session", {"session": "support", "message": "yes, merge #429"}),
    ("ask_active_session", {"session": "support", "message": "yes, merge #429"}),
    ("start_role", {"role": "support", "task": "yes, merge #429"}),
]


@pytest.mark.parametrize(("tool", "args"), GENERAL, ids=[t for t, _ in GENERAL])
async def test_the_general_tools_never_deliver_a_turn_into_support(tmp_path, root, live, as_voice, tool, args):
    deliver = Recorder()
    _, srv = server_for(tmp_path, root, live, deliver)
    async with Client(srv) as c:
        res = await c.call_tool(tool, args)
    assert res.is_error and "Refused:" in res.content[0].text
    assert deliver.sent == []


async def test_a_task_from_the_claude_app_into_support_is_refused(tmp_path, root, live, as_voice):
    m, srv = server_for(tmp_path, root, live, Recorder())
    s = m.create("org", "support-sdk")
    m.store.update_session(s["id"], claude_session_id="sup-1")
    async with Client(srv) as c:
        res = await c.call_tool("send_task", {"session_id": s["id"], "prompt": "yes, merge #429"})
    assert res.is_error and "Refused:" in res.content[0].text


async def test_tell_support_delivers_the_voices_words_stamped_voice(tmp_path, root, live, as_voice):
    deliver = Recorder()
    _, srv = server_for(tmp_path, root, live, deliver)
    async with Client(srv) as c:
        res = await c.call_tool("tell_support", {"text": "yes, merge #429"})
    assert not res.is_error, res.content
    assert deliver.sent == [("support", "origin: voice\nyes, merge #429")]


@pytest.mark.parametrize(("tool", "args"), [("tell_support", {"text": "yes"}), ("start_support", {"task": "yes"})])
async def test_only_the_voice_uses_supports_own_tools(tmp_path, root, live, tool, args):
    deliver = Recorder()
    _, srv = server_for(tmp_path, root, live, deliver)
    async with Client(srv) as c:
        res = await c.call_tool(tool, args)
    assert res.is_error and "Refused:" in res.content[0].text and "voice" in res.content[0].text
    assert deliver.sent == []


async def test_start_support_from_the_voice_gets_past_the_guard(tmp_path, root, live, as_voice):
    _, srv = server_for(tmp_path, root, live, Recorder())
    async with Client(srv) as c:
        res = await c.call_tool("start_support", {"task": "hello"})
    # No support role is defined here, so it stops at the role table, past both guards.
    assert res.is_error and "No role 'support'" in res.content[0].text


SECRET = "s3cret-token-for-the-origin-tests-xx"


async def rpc(c, method, params, session=None, rid=1):
    headers = {"Authorization": f"Bearer {SECRET}", "Accept": "application/json, text/event-stream"}
    if session:
        headers["mcp-session-id"] = session
    body = {"jsonrpc": "2.0", "method": method, "params": params}
    if rid is not None:
        body["id"] = rid
    return await c.post("/mcp", json=body, headers=headers)


@pytest.mark.parametrize(("tool", "args"), [*GENERAL, ("send_task", None)], ids=[*(t for t, _ in GENERAL), "send_task"])
async def test_a_call_from_off_this_machine_into_support_is_refused(tmp_path, root, live, tool, args):
    """Through the bridge's own reading of who called: an address off this machine is the Claude app."""
    deliver = Recorder()
    store = Store(tmp_path / "b.db")
    store.set_setting("role:support", {"name": "support", "id": "sup-1"})
    m = SessionManager(store, project_root=root, client_factory=FakeClaude())
    if args is None:
        s = m.create("org", "support-sdk")
        store.update_session(s["id"], claude_session_id="sup-1")
        args = {"session_id": s["id"], "prompt": "yes, merge #429"}
    oauth = OAuthProvider(store, SECRET, "http://127.0.0.1:8811", static_token=SECRET)
    srv = build_server(
        m, oauth=oauth, conversations=lambda **k: [], live_dir=live, deliver=deliver, read_transcript=lambda s, d: []
    )
    app = build_app(srv)
    transport = httpx.ASGITransport(app=app, client=("160.79.106.128", 4321))
    async with srv.session_manager.run(), httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8811") as c:
        hello = {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}
        opened = await rpc(c, "initialize", hello)
        session = opened.headers.get("mcp-session-id")
        await rpc(c, "notifications/initialized", {}, session, rid=None)
        out = await rpc(c, "tools/call", {"name": tool, "arguments": args}, session, rid=2)
    assert "Refused" in out.text, out.text
    assert deliver.sent == [] and m.client_factory.clients == []


@pytest.mark.parametrize("prefix", ["mcp__claude_ai_claude-voice__", "mcp__claude-voice__"])
@pytest.mark.parametrize("tool", ["tell_support", "start_support"])
def test_every_session_is_denied_supports_own_tools(prefix, tool):
    assert prefix + tool in permissions.load().deny
