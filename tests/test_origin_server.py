"""Through the bridge's tools: each delivered turn is stamped, and no session reaches support."""

import json
import os

import pytest
from fakes import FakeClaude
from mcp.client import Client

from claude_voice import transcripts
from claude_voice.server import build_server
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
def as_voice():
    token = transcripts.current_client.set("claude_app")
    yield
    transcripts.current_client.reset(token)


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
