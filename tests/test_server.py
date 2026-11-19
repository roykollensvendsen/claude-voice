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
        "fleet_recap",
        "cancel",
        "close_session",
        "list_claude_conversations",
        "attach_conversation",
        "list_pending_approvals",
        "approve",
        "deny",
    } <= names


async def test_a_voice_round_trip(tmp_path, root):
    m, server = make(tmp_path, root, FakeClaude([init("c-1"), say("On it."), result("Done.", "c-1")]))
    async with Client(server) as client:
        s = await call(client, "create_session", project="app", label="demo")
        sent = await call(client, "send_task", session_id=s["id"], prompt="do it")
        assert sent["status"] == "running"
        await m.wait(s["id"])

        recap = await call(client, "session_recap", session_id=s["id"])
        assert recap["last_result"] == "Done."
        assert [e["kind"] for e in m.store.events(s["id"])] == ["prompt", "text", "result"]

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
        out = await call(c, "list_projects", query="ap")
    assert [p["name"] for p in out["projects"]] == ["app"]


async def test_health_check_needs_no_token(http_server):
    async with httpx.AsyncClient() as h:
        r = await h.get(f"{http_server}/healthz")
    assert r.status_code == 200


# -- configuration -------------------------------------------------------------


def test_config_refuses_an_api_key_so_the_subscription_is_used(tmp_path):
    env = {"CLAUDE_VOICE_ROOT": str(tmp_path), "ANTHROPIC_API_KEY": "sk-ant-x"}
    with pytest.raises(ConfigError, match="ANTHROPIC_API_KEY"):
        load_config(env, transport="stdio")


def test_config_reads_the_access_token_lifetime(tmp_path):
    env = {"CLAUDE_VOICE_ROOT": str(tmp_path)}
    assert load_config(env, transport="stdio").access_ttl == 3600
    env["CLAUDE_VOICE_ACCESS_TOKEN_SECONDS"] = "604800"
    assert load_config(env, transport="stdio").access_ttl == 604800


@pytest.mark.parametrize("bad", ["a week", "-5", "30", "1.5"])
def test_an_access_token_lifetime_that_is_not_a_whole_number_of_at_least_a_minute_stops_the_bridge(tmp_path, bad):
    env = {"CLAUDE_VOICE_ROOT": str(tmp_path), "CLAUDE_VOICE_ACCESS_TOKEN_SECONDS": bad}
    with pytest.raises(ConfigError, match="CLAUDE_VOICE_ACCESS_TOKEN_SECONDS"):
        load_config(env, transport="stdio")


def test_an_access_token_lifetime_written_with_a_superscript_stops_the_bridge(tmp_path):
    """isdigit() takes "²", which int() cannot read: a plain refusal, not a crash."""
    env = {"CLAUDE_VOICE_ROOT": str(tmp_path), "CLAUDE_VOICE_ACCESS_TOKEN_SECONDS": "3600²"}
    with pytest.raises(ConfigError, match="CLAUDE_VOICE_ACCESS_TOKEN_SECONDS"):
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


def test_a_ui_folder_is_used_when_it_exists_and_stops_the_bridge_when_it_does_not(tmp_path):
    env = {"CLAUDE_VOICE_ROOT": str(tmp_path)}
    (tmp_path / "ui").mkdir()
    assert load_config({**env, "CLAUDE_VOICE_UI_DIR": f" {tmp_path / 'ui'} "}, "stdio").ui_dir == tmp_path / "ui"
    with pytest.raises(ConfigError, match="CLAUDE_VOICE_UI_DIR is not a directory"):
        load_config({**env, "CLAUDE_VOICE_UI_DIR": str(tmp_path / "nope")}, "stdio")


async def test_approvals_can_be_answered_through_tools(tmp_path, root):
    from claude_agent_sdk import PermissionResultAllow, ToolPermissionContext
    from fakes import Call

    from claude_voice.approvals import ApprovalBroker

    outcomes = []

    async def ask(options):
        outcomes.append(await options.can_use_tool("Bash", {"command": "make"}, ToolPermissionContext()))

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
        "fleet_recap",
        "list_claude_conversations",
        "list_pending_approvals",
        "list_active_sessions",
        "read_session_output",
        "search_session_history",
        "whats_new",
        "list_replies",
        "update_changes",
        "session_tree",
        "org_chart",
        "health",
        "get_active_session",
        "check_dependencies",
        "digest_session",
        "side_question",
        "wait_for_session",
        "developer_mode_request",  # a question only; the confirm is what switches
        "plugin_approve_request",
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


async def test_an_attached_conversation_is_named_by_its_title_then_its_summary_unless_one_is_given(tmp_path, root):
    import dataclasses

    titled = dataclasses.replace(conversation("t-1", str(root / "app"), "Fix login"), custom_title="Login work")
    convs = FakeConversations([titled, conversation("t-2", str(root / "lib"), "Refactor")])
    m = SessionManager(Store(tmp_path / "b.db"), project_root=root, client_factory=FakeClaude())
    async with Client(build_server(m, conversations=convs)) as client:
        by_title = await call(client, "attach_conversation", claude_session_id="t-1")
        by_summary = await call(client, "attach_conversation", claude_session_id="t-2")
        given = await call(client, "attach_conversation", claude_session_id="t-2", label="mine")
    assert (by_title["label"], by_summary["label"], given["label"]) == ("Login work", "Refactor", "mine")


async def test_attach_with_a_project_named_does_not_look_the_conversation_up(tmp_path, root):
    m = SessionManager(Store(tmp_path / "b.db"), project_root=root, client_factory=FakeClaude())
    async with Client(build_server(m, conversations=FakeConversations([]))) as client:
        s = await call(client, "attach_conversation", claude_session_id="t-9", project="lib")
    assert (s["project_path"], s["label"]) == (str(root / "lib"), None)


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
            "kind": "interactive",
            "managed_by_bridge": False,
            "minutes_since_update": 2,
            "number": 1,
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
    assert any(line.startswith("mcp tools/call list_projects -> ok ") and "trace=-" in line for line in lines)
    # A call aimed at a session says which, and every call says who made it.
    assert any(
        line.startswith("mcp tools/call session_recap -> error ")
        and "session=nope" in line
        and line.endswith("from=in_process")
        for line in lines
    )


async def test_a_cancelled_tool_call_is_logged_as_cancelled(caplog):
    """A client that gives up on a call cancels it; that must show in the log, not vanish."""
    import logging
    from types import SimpleNamespace

    from claude_voice.metrics import METRICS
    from claude_voice.server import log_requests

    METRICS.reset()
    ctx = SimpleNamespace(method="tools/call", params={"name": "message_active_session"}, request_id=7)

    async def gives_up(_ctx):
        raise asyncio.CancelledError

    with caplog.at_level(logging.INFO, logger="claude_voice"), pytest.raises(asyncio.CancelledError):
        await log_requests(ctx, gives_up)
    lines = [r.getMessage() for r in caplog.records if r.name == "claude_voice"]
    assert any(line.startswith("mcp tools/call message_active_session -> cancelled ") for line in lines)
    assert METRICS.snapshot()["errors"][0]["kind"] == "cancelled"


@pytest.mark.parametrize("method", ["tools/call", "resources/read"])
async def test_a_call_that_raises_is_logged_with_its_error_and_only_a_tool_call_is_counted(caplog, method):
    import logging
    from types import SimpleNamespace

    from claude_voice.metrics import METRICS
    from claude_voice.server import log_requests

    METRICS.reset()
    ctx = SimpleNamespace(method=method, params={"name": "list_projects"}, request_id=8)

    async def breaks(_ctx):
        raise RuntimeError("disk full")

    with caplog.at_level(logging.INFO, logger="claude_voice"), pytest.raises(RuntimeError):
        await log_requests(ctx, breaks)
    lines = [r.getMessage() for r in caplog.records if r.name == "claude_voice"]
    assert any(line.startswith(f"mcp {method} list_projects -> RuntimeError ") for line in lines)
    errors = METRICS.snapshot()["errors"]
    if method == "tools/call":
        assert (errors[0]["kind"], errors[0]["error"]) == ("exception", "RuntimeError: disk full")
    else:
        assert errors == []


async def test_a_cancelled_call_that_is_not_a_tool_call_is_logged_but_not_counted(caplog):
    import logging
    from types import SimpleNamespace

    from claude_voice.metrics import METRICS
    from claude_voice.server import log_requests

    METRICS.reset()
    ctx = SimpleNamespace(method="resources/read", params={}, request_id=9)

    async def gives_up(_ctx):
        raise asyncio.CancelledError

    with caplog.at_level(logging.INFO, logger="claude_voice"), pytest.raises(asyncio.CancelledError):
        await log_requests(ctx, gives_up)
    lines = [r.getMessage() for r in caplog.records if r.name == "claude_voice"]
    assert any(line.startswith("mcp resources/read -> cancelled ") for line in lines)
    assert METRICS.snapshot()["errors"] == []


async def test_a_trace_id_sent_with_a_call_is_logged_with_it_and_its_time(tmp_path, root, caplog):
    import logging
    import re

    _, server = make(tmp_path, root, FakeClaude())
    with caplog.at_level(logging.INFO, logger="claude_voice"):
        async with Client(server) as client:
            await client.call_tool("list_projects", {}, meta={"trace_id": "ab12cd34ef56"})
    lines = [r.getMessage() for r in caplog.records if r.name == "claude_voice"]
    assert any(
        re.fullmatch(r"mcp tools/call list_projects -> ok \d+ms trace=ab12cd34ef56 from=\w+", line) for line in lines
    )


async def test_every_call_is_counted_with_its_time_and_outcome(tmp_path, root):
    from claude_voice.metrics import METRICS

    METRICS.reset()
    _, server = make(tmp_path, root, FakeClaude())
    async with Client(server) as client:
        await client.call_tool("list_projects", {})
        await client.call_tool("list_projects", {})
        await client.call_tool("session_recap", {"session_id": "nope"}, meta={"trace_id": "t1"})
    snap = METRICS.snapshot()
    tools = {t["name"]: t for t in snap["tools"]}
    assert tools["list_projects"]["calls"] == 2 and tools["list_projects"]["errors"] == 0
    assert tools["session_recap"]["errors"] == 1
    assert (
        tools["list_projects"]["p50_ms"] >= 0 and tools["list_projects"]["p95_ms"] >= tools["list_projects"]["p50_ms"]
    )
    err = snap["errors"][0]
    assert err["tool"] == "session_recap" and err["trace_id"] == "t1" and err["kind"] == "tool_error"
    assert "nope" in err["error"] and len(err["error"]) <= 200


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


def test_the_courier_name_comes_from_the_environment(tmp_path):
    env = {"CLAUDE_VOICE_ROOT": str(tmp_path)}
    assert load_config(env, "stdio").courier_name == "Owner via claude-voice"
    named = {**env, "CLAUDE_VOICE_COURIER_NAME": "Roy via stemmen"}
    assert load_config(named, "stdio").courier_name == "Roy via stemmen"


def test_the_log_is_kept_in_a_file_next_to_the_database(tmp_path):
    import logging

    from claude_voice.server import configure_logging

    level = logging.getLogger().level
    handler = configure_logging(tmp_path / "state" / "bridge.log")
    try:
        logging.getLogger("claude_voice.oauth").info("oauth refresh ok for client abcdef12 (family 123456)")
        handler.flush()
    finally:
        logging.getLogger().removeHandler(handler)
        logging.getLogger().setLevel(level)
        handler.close()
    text = (tmp_path / "state" / "bridge.log").read_text(encoding="utf-8")
    assert "claude_voice.oauth: oauth refresh ok for client abcdef12" in text


class HeldLook:
    """Every tree build waits until the test lets it go, as the first one does on a busy machine,
    where it reads each running session's whole conversation.

    `began` is set when a look starts and `ended` when one finishes, so a test proves an answer
    came first by the order of events, never by a clock: a slow runner cannot fail it. A look
    still held after `HOLD` seconds goes on, so a bridge that waits on its look ends the test
    with a wrong order, not a hang. HOLD is that hang breaker, not a timing check: lowering it
    only brings the old flake back.
    """

    HOLD = 15

    def __init__(self, monkeypatch):
        import threading

        from claude_voice import transcripts
        from claude_voice.tree import SessionTree

        self.began = threading.Event()
        self.ended = threading.Event()
        self._go = threading.Event()
        build = SessionTree.build

        def held(tree):
            self.began.set()
            self._go.wait(self.HOLD)
            try:
                return build(tree)
            finally:
                self.ended.set()

        async def warm():
            return None

        monkeypatch.setattr(SessionTree, "build", held)
        monkeypatch.setattr(transcripts.shared_reader(), "warm", warm)

    def let_go(self):
        self._go.set()


def watched(tmp_path, root):
    m = SessionManager(Store(tmp_path / "bridge.db"), project_root=root, client_factory=FakeClaude())
    return build_server(m, live_dir=tmp_path / "live", projects_dir=tmp_path / "projects", watch_interval=60)


async def test_the_bridge_answers_as_soon_as_it_has_started_while_its_first_look_runs_behind(
    tmp_path, root, monkeypatch
):
    look = HeldLook(monkeypatch)
    async with Client(watched(tmp_path, root)) as client:
        try:
            await call(client, "list_pending_approvals")
            answered_during_the_look = not look.ended.is_set()
        finally:
            look.let_go()
        for _ in range(100):  # the first look still happens, behind
            looked = (await call(client, "health"))["watcher"]["last_check_at"]
            if looked:
                break
            await asyncio.sleep(0.05)
    assert answered_during_the_look
    assert looked


async def test_a_question_about_news_does_not_wait_for_a_look_already_under_way(tmp_path, root, monkeypatch):
    look = HeldLook(monkeypatch)
    async with Client(watched(tmp_path, root)) as client:
        try:
            assert await asyncio.to_thread(look.began.wait, HeldLook.HOLD)
            news = await call(client, "whats_new")
            answered_during_the_look = not look.ended.is_set()
        finally:
            look.let_go()
    assert news["cursor"]
    assert answered_during_the_look


async def test_the_bridge_removes_the_helper_folders_a_killed_bridge_left_when_it_starts(tmp_path, root, monkeypatch):
    import os
    import sys
    import time

    from claude_voice import transcripts

    if sys.platform == "win32":
        pytest.skip("Windows has no flock; there the sweep does nothing")
    look = HeldLook(monkeypatch)
    left = transcripts.HELPERS_IN / "claude-voice-msg-left"
    left.mkdir()
    an_hour_ago = time.time() - 2 * 60 * 60
    os.utime(left, (an_hour_ago, an_hour_ago))
    async with Client(watched(tmp_path, root)) as client:
        look.let_go()
        await call(client, "health")
    assert not left.exists()
