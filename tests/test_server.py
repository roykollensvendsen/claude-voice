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

from claude_voice.oauth import OAuthProvider
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
    assert not res.is_error, res.content
    return res.structured_content


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
        "list_pending_approvals",
        "approve",
        "deny",
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
        assert bad.is_error and "under" in bad.content[0].text

        s = await call(client, "create_session", project="app")
        await call(client, "send_task", session_id=s["id"], prompt="go")
        await pause.reached.wait()
        busy = await client.call_tool("send_task", {"session_id": s["id"], "prompt": "again"})
        assert busy.is_error and "still working" in busy.content[0].text

        outside = await client.call_tool("list_sessions", {"project": "/etc"})
        assert outside.is_error and "under" in outside.content[0].text

        unknown = await client.call_tool("session_recap", {"session_id": "nope"})
        assert unknown.is_error and "no session" in unknown.content[0].text.lower()

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
    port = free_port()
    store = Store(tmp_path / "bridge.db")
    oauth = OAuthProvider(store, TOKEN, f"http://127.0.0.1:{port}", static_token=TOKEN)
    m = SessionManager(store, project_root=root, client_factory=FakeClaude())
    server = build_server(m, oauth=oauth)
    config = uvicorn.Config(build_app(server), port=port, log_level="warning")
    uv = uvicorn.Server(config)
    task = asyncio.create_task(uv.serve())
    while not uv.started:  # noqa: ASYNC110 - uvicorn exposes no event
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


async def test_approvals_can_be_answered_through_tools(tmp_path, root):
    from claude_agent_sdk import PermissionResultAllow, ToolPermissionContext
    from fakes import Call

    from claude_voice.approvals import ApprovalBroker

    outcomes = []

    async def ask(options):
        outcomes.append(
            await options.can_use_tool("Bash", {"command": "make"}, ToolPermissionContext())
        )

    store = Store(tmp_path / "bridge.db")
    broker = ApprovalBroker(store)
    claude = FakeClaude([Call(ask), result("built", "c-1")])
    m = SessionManager(store, project_root=root, client_factory=claude, approvals=broker)
    async with Client(build_server(m)) as client:
        s = await call(client, "create_session", project="app")
        await call(client, "send_task", session_id=s["id"], prompt="build")
        for _ in range(200):
            pending = (await call(client, "list_pending_approvals"))["approvals"]
            if pending:
                break
            await asyncio.sleep(0.01)
        assert pending[0]["tool"] == "Bash"

        bad = await client.call_tool("approve", {"approval_id": "zz"})
        assert bad.is_error

        await call(client, "approve", approval_id=pending[0]["id"])
        await m.wait(s["id"])
    assert isinstance(outcomes[0], PermissionResultAllow)


def test_public_url_comes_from_the_tunnel_host(tmp_path):
    env = {"CLAUDE_VOICE_ROOT": str(tmp_path), "CLAUDE_VOICE_TOKEN": "x" * 32}
    assert load_config(env, "http").public_url == "http://127.0.0.1:8811"
    hosts = {**env, "CLAUDE_VOICE_PUBLIC_HOSTS": "me.ts.net:10000"}
    assert load_config(hosts, "http").public_url == "https://me.ts.net:10000"
    explicit = {**hosts, "CLAUDE_VOICE_PUBLIC_URL": "https://voice.example/"}
    assert load_config(explicit, "http").public_url == "https://voice.example"


async def test_status_tools_are_marked_read_only_so_clients_need_no_confirmation(tmp_path, root):
    _, server = make(tmp_path, root, FakeClaude())
    async with Client(server) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}
    read_only = {
        "list_projects",
        "list_sessions",
        "session_recap",
        "get_messages",
        "recent_activity",
        "fleet_recap",
        "list_claude_conversations",
        "list_pending_approvals",
        "list_active_sessions",
        "read_session_output",
        "search_session_history",
        "whats_new",
    }
    for name in read_only:
        ann = tools[name].annotations
        assert ann is not None and ann.read_only_hint is True, name
    for name in set(tools) - read_only:
        ann = tools[name].annotations
        assert ann is None or not ann.read_only_hint, name
    assert tools["approve"].annotations.destructive_hint is True
    assert tools["close_session"].annotations.destructive_hint is False


class FakeConversations:
    """Stands in for the SDK's on-disk Claude Code session index."""

    def __init__(self, items):
        self.items = items

    def __call__(self, directory=None, limit=None):
        found = [c for c in self.items if directory is None or c.cwd == directory]
        return found[:limit]


def conversation(sid, cwd, title, minutes_ago=1):
    import time

    from claude_agent_sdk import SDKSessionInfo

    return SDKSessionInfo(
        session_id=sid,
        summary=title,
        last_modified=int((time.time() - minutes_ago * 60) * 1000),
        cwd=cwd,
        first_prompt="hi",
    )


async def test_conversations_from_every_project_under_the_root_are_listed(tmp_path, root):
    convs = FakeConversations(
        [
            conversation("t-1", str(root / "app"), "Fix login"),
            conversation("t-2", str(root / "lib"), "Refactor"),
            conversation("t-3", "/somewhere/else", "Private"),
        ]
    )
    m = SessionManager(Store(tmp_path / "b.db"), project_root=root, client_factory=FakeClaude())
    async with Client(build_server(m, conversations=convs)) as client:
        out = await call(client, "list_claude_conversations")
    listed = [(c["claude_session_id"], c["project"]) for c in out["conversations"]]
    assert listed == [("t-1", "app"), ("t-2", "lib")]
    assert out["conversations"][0]["summary"] == "Fix login"
    assert out["conversations"][0]["minutes_ago"] == 1


async def test_attach_finds_the_project_from_the_conversation(tmp_path, root):
    convs = FakeConversations([conversation("t-2", str(root / "lib"), "Refactor")])
    claude = FakeClaude([result("back", "t-2")])
    m = SessionManager(Store(tmp_path / "b.db"), project_root=root, client_factory=claude)
    async with Client(build_server(m, conversations=convs)) as client:
        s = await call(client, "attach_conversation", claude_session_id="t-2")
        assert s["project_path"] == str(root / "lib")
        await call(client, "send_task", session_id=s["id"], prompt="where were we?")
        await m.wait(s["id"])
    assert claude.clients[0].options.resume == "t-2"


async def test_attach_refuses_conversations_outside_the_root(tmp_path, root):
    convs = FakeConversations([conversation("t-3", "/somewhere/else", "Private")])
    m = SessionManager(Store(tmp_path / "b.db"), project_root=root, client_factory=FakeClaude())
    async with Client(build_server(m, conversations=convs)) as client:
        bad = await client.call_tool("attach_conversation", {"claude_session_id": "t-3"})
        missing = await client.call_tool("attach_conversation", {"claude_session_id": "nope"})
    assert bad.is_error and "under" in bad.content[0].text
    assert missing.is_error and "no claude code conversation" in missing.content[0].text.lower()


def live_entry(directory, pid, sid, cwd, name, status="busy", minutes_ago=2):
    import json
    import time

    now = time.time() * 1000
    (directory / f"{pid}.json").write_text(
        json.dumps(
            {
                "pid": pid,
                "sessionId": sid,
                "cwd": cwd,
                "name": name,
                "status": status,
                "kind": "interactive",
                "updatedAt": now - minutes_ago * 60000,
            }
        )
    )


async def test_active_sessions_are_the_running_claude_code_processes(tmp_path, root):
    import os

    live = tmp_path / "live"
    live.mkdir()
    live_entry(live, os.getpid(), "c-1", str(root / "app"), "app-fix", "busy")
    live_entry(live, 2**22 + 12345, "c-2", str(root / "lib"), "dead-one", "idle")
    (live / "garbage.json").write_text("{not json")
    convs = FakeConversations([conversation("c-1", str(root / "app"), "Fix")])
    m = SessionManager(Store(tmp_path / "b.db"), project_root=root, client_factory=FakeClaude())
    async with Client(build_server(m, conversations=convs, live_dir=live)) as client:
        active = await call(client, "list_active_sessions")
        listed = await call(client, "list_claude_conversations")

    assert active["sessions"] == [
        {
            "claude_session_id": "c-1",
            "name": "app-fix",
            "project": "app",
            "status": "busy",
            "minutes_since_update": 2,
        }
    ]
    assert listed["conversations"][0]["open_in_terminal"] is True


async def test_tool_calls_are_logged_by_name(tmp_path, root, caplog):
    import logging

    _, server = make(tmp_path, root, FakeClaude())
    with caplog.at_level(logging.INFO, logger="claude_voice"):
        async with Client(server) as client:
            await call(client, "list_projects")
            await client.call_tool("session_recap", {"session_id": "nope"})
    lines = [r.getMessage() for r in caplog.records if r.name == "claude_voice"]
    assert "mcp tools/call list_projects -> ok" in lines
    assert "mcp tools/call session_recap -> error" in lines


async def test_list_sessions_also_shows_running_claude_code_sessions(tmp_path, root):
    import os

    live = tmp_path / "live"
    live.mkdir()
    live_entry(live, os.getpid(), "c-1", str(root / "app"), "app-fix", "idle")
    m = SessionManager(Store(tmp_path / "b.db"), project_root=root, client_factory=FakeClaude())
    async with Client(build_server(m, conversations=FakeConversations([]), live_dir=live)) as c:
        out = await call(c, "list_sessions")
    assert out["sessions"] == []
    assert [s["name"] for s in out["running_claude_code_sessions"]] == ["app-fix"]
