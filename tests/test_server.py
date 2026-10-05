import asyncio
import socket

import httpx
import pytest
import uvicorn
from fakes import FakeClaude, Pause, init, result, say
from mcp.client import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.server.mcpserver.exceptions import ToolError
from mcp.shared._httpx_utils import create_mcp_http_client

from claude_voice.server import ConfigError, build_app, build_server, load_config
from claude_voice.sessions import SessionManager
from claude_voice.store import Store

TOKEN = "s3cret-token-for-tests"


@pytest.fixture
def root(tmp_path):
    r = tmp_path / "src"
    (r / "app").mkdir(parents=True)
    (r / "lib").mkdir()
    (r / ".hidden").mkdir()
    return r


def make(tmp_path, root, claude):
    m = SessionManager(Store(tmp_path / "bridge.db"), project_root=root, client_factory=claude)
    return m, build_server(m)


async def call(client, name, **args):
    res = await client.call_tool(name, args)
    assert not res.isError, res.content
    return res.structuredContent


async def test_exposes_the_voice_control_tools(tmp_path, root):
    _, server = make(tmp_path, root, FakeClaude())
    async with Client(server) as client:
        names = {t.name for t in (await client.list_tools()).tools}
    assert {
        "list_projects",
        "create_session",
        "list_sessions",
        "send_task",
        "session_recap",
        "get_messages",
        "recent_activity",
        "fleet_recap",
        "cancel",
        "close_session",
        "list_claude_conversations",
        "attach_conversation",
    } <= names


async def test_list_projects_shows_visible_directories_under_the_root(tmp_path, root):
    _, server = make(tmp_path, root, FakeClaude())
    async with Client(server) as client:
        out = await call(client, "list_projects")
    assert out["projects"] == ["app", "lib"]


async def test_a_voice_round_trip(tmp_path, root):
    m, server = make(
        tmp_path, root, FakeClaude([init("c-1"), say("On it."), result("Done.", "c-1")])
    )
    async with Client(server) as client:
        s = await call(client, "create_session", project="app", label="demo")
        sent = await call(client, "send_task", session_id=s["id"], prompt="do it")
        assert sent["status"] == "running"
        await m.wait(s["id"])

        recap = await call(client, "session_recap", session_id=s["id"])
        assert recap["last_result"] == "Done."
        msgs = await call(client, "get_messages", session_id=s["id"])
        assert [e["kind"] for e in msgs["events"]] == ["prompt", "text", "result"]
        assert msgs["next_after"] == msgs["events"][-1]["seq"]

        listed = await call(client, "list_sessions")
        assert [x["label"] for x in listed["sessions"]] == ["demo"]


async def test_mistakes_come_back_as_readable_tool_errors(tmp_path, root):
    pause = Pause()
    m, server = make(tmp_path, root, FakeClaude([pause]))
    async with Client(server) as client:
        bad = await client.call_tool("create_session", {"project": "/etc"})
        assert bad.isError and "under" in bad.content[0].text

        s = await call(client, "create_session", project="app")
        await call(client, "send_task", session_id=s["id"], prompt="go")
        await pause.reached.wait()
        busy = await client.call_tool("send_task", {"session_id": s["id"], "prompt": "again"})
        assert busy.isError and "still working" in busy.content[0].text

        unknown = await client.call_tool("session_recap", {"session_id": "nope"})
        assert unknown.isError and "no session" in unknown.content[0].text.lower()

        await call(client, "cancel", session_id=s["id"])


async def test_tool_errors_are_tool_errors_not_crashes(tmp_path, root):
    _, server = make(tmp_path, root, FakeClaude())
    with pytest.raises(ToolError):
        await server.call_tool("session_recap", {"session_id": "nope"})


# -- over HTTP -----------------------------------------------------------------


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
async def http_server(tmp_path, root):
    _, server = make(tmp_path, root, FakeClaude())
    port = free_port()
    config = uvicorn.Config(build_app(server, TOKEN), port=port, log_level="warning")
    uv = uvicorn.Server(config)
    task = asyncio.create_task(uv.serve())
    while not uv.started:
        await asyncio.sleep(0.01)
    yield f"http://127.0.0.1:{port}"
    uv.should_exit = True
    await task


@pytest.mark.parametrize("auth", [None, "Bearer wrong", f"Basic {TOKEN}", TOKEN])
async def test_http_refuses_requests_without_the_right_token(http_server, auth):
    headers = {"Authorization": auth} if auth else {}
    async with httpx.AsyncClient() as h:
        r = await h.post(f"{http_server}/mcp", headers=headers, json={})
    assert r.status_code == 401
    assert r.headers["www-authenticate"].startswith("Bearer")


async def test_http_serves_mcp_with_the_right_token(http_server):
    http = create_mcp_http_client(headers={"Authorization": f"Bearer {TOKEN}"})
    async with http, Client(streamable_http_client(f"{http_server}/mcp", http_client=http)) as c:
        out = await call(c, "list_projects")
    assert out["projects"] == ["app", "lib"]


async def test_health_check_needs_no_token(http_server):
    async with httpx.AsyncClient() as h:
        r = await h.get(f"{http_server}/healthz")
    assert r.status_code == 200


# -- configuration -------------------------------------------------------------


def test_config_refuses_an_api_key_so_the_subscription_is_used(tmp_path):
    env = {"CLAUDE_VOICE_ROOT": str(tmp_path), "ANTHROPIC_API_KEY": "sk-ant-x"}
    with pytest.raises(ConfigError, match="ANTHROPIC_API_KEY"):
        load_config(env, transport="stdio")


def test_config_allows_an_api_key_when_explicitly_wanted(tmp_path):
    env = {
        "CLAUDE_VOICE_ROOT": str(tmp_path),
        "ANTHROPIC_API_KEY": "sk-ant-x",
        "CLAUDE_VOICE_ALLOW_API_KEY": "1",
    }
    assert load_config(env, transport="stdio").root == tmp_path


def test_http_needs_a_long_token(tmp_path):
    env = {"CLAUDE_VOICE_ROOT": str(tmp_path)}
    with pytest.raises(ConfigError, match="CLAUDE_VOICE_TOKEN"):
        load_config(env, transport="http")
    with pytest.raises(ConfigError, match="at least"):
        load_config({**env, "CLAUDE_VOICE_TOKEN": "short"}, transport="http")
    ok = load_config({**env, "CLAUDE_VOICE_TOKEN": "x" * 32}, transport="http")
    assert ok.host == "127.0.0.1"


def test_root_must_exist(tmp_path):
    with pytest.raises(ConfigError, match="CLAUDE_VOICE_ROOT"):
        load_config({"CLAUDE_VOICE_ROOT": str(tmp_path / "nope")}, transport="stdio")
