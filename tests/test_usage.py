"""Usage: the plan's limits and each session's context from the status line, and voice activity through the bridge."""

import json
import os

import httpx
import pytest
from fakes import FakeClaude
from mcp.client import Client

from claude_voice import usage
from claude_voice.oauth import OAuthProvider
from claude_voice.server import build_app, build_server
from claude_voice.sessions import SessionManager
from claude_voice.store import Store

NOW = 1_800_000_000.0
SECRET = "s3cret-token-for-the-usage-tests-xx"
BEARER = {"Authorization": f"Bearer {SECRET}"}


def statusline(folder, sid, at, context=None, limits=None):
    folder.mkdir(parents=True, exist_ok=True)
    body = {"session_id": sid, "at": at}
    if context is not None:
        body["context_window"] = {"used_percentage": context, "context_window_size": 200000}
    if limits is not None:
        body["rate_limits"] = limits
    (folder / f"{sid}.json").write_text(json.dumps(body))


def limits(five, seven, reset_in=3600):
    return {
        "five_hour": {"used_percentage": five, "resets_at": NOW + reset_in},
        "seven_day": {"used_percentage": seven, "resets_at": NOW + 5 * 86400},
    }


def test_the_plan_comes_from_the_newest_status_line_that_had_limits(tmp_path):
    folder = tmp_path / "statusline"
    statusline(folder, "old", NOW - 600, limits=limits(10, 5))
    statusline(folder, "new", NOW - 60, limits=limits(42, 17))
    statusline(folder, "none", NOW - 10, context=30)  # a newer line without limits does not count
    plan = usage.plan(folder, now=NOW)
    assert plan["five_hour"]["used_percentage"] == 42 and plan["seven_day"]["used_percentage"] == 17
    assert plan["five_hour"]["stale"] is False and plan["as_of"]
    assert "chat and voice" in plan["label"]


def test_a_limit_past_its_reset_is_marked_stale_and_no_figures_mean_no_plan(tmp_path):
    folder = tmp_path / "statusline"
    assert usage.plan(folder, now=NOW) is None
    statusline(folder, "s", NOW - 7200, limits=limits(90, 50, reset_in=-60))
    assert usage.plan(folder, now=NOW)["five_hour"]["stale"] is True


@pytest.mark.parametrize(("used", "status"), [(50, None), (85, "allowed_warning"), (100, "rejected")])
def test_a_warning_is_raised_as_the_plan_nears_its_limit(tmp_path, used, status):
    folder = tmp_path / "statusline"
    statusline(folder, "s", NOW - 60, limits=limits(used, 10))
    warning = usage.warning(usage.plan(folder, now=NOW))
    assert (warning or {}).get("status") == status


def test_no_dollar_figure_is_ever_passed_on(tmp_path):
    folder = tmp_path / "statusline"
    statusline(folder, "s", NOW - 60, context=40, limits=limits(20, 10))
    raw = json.loads((folder / "s.json").read_text())
    raw["cost"] = {"total_cost_usd": 12.5}
    (folder / "s.json").write_text(json.dumps(raw))
    shown = json.dumps([usage.plan(folder, now=NOW), usage.context(folder, "s")])
    assert "cost" not in shown and "usd" not in shown.lower()


def test_each_sessions_context_comes_from_its_own_status_line(tmp_path):
    folder = tmp_path / "statusline"
    statusline(folder, "s1", NOW - 30, context=63)
    assert usage.context(folder, "s1") == {"used_percentage": 63, "as_of": usage.iso(NOW - 30)}
    assert usage.context(folder, "unknown") is None


# -- voice activity, as the bridge sees it ---------------------------------------------


@pytest.fixture
def bridge(tmp_path):
    (tmp_path / "home").mkdir()
    store = Store(tmp_path / "b.db")
    oauth = OAuthProvider(store, SECRET, "http://127.0.0.1:8811", static_token=SECRET)
    m = SessionManager(store, project_root=tmp_path / "home", client_factory=FakeClaude())
    server = build_server(
        m,
        oauth=oauth,
        conversations=lambda **k: [],
        live_dir=tmp_path / "live",
        statusline_dir=tmp_path / "statusline",
    )
    return server, build_app(server), store


def from_address(app, address):
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=(address, 4321)), base_url="http://127.0.0.1:8811"
    )


async def call_tool(c, name, trace=None, session="conn-1"):
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": {}}}
    if trace:
        body["params"]["_meta"] = {"trace_id": trace}
    headers = {**BEARER, "Accept": "application/json, text/event-stream", "mcp-session-id": session}
    return await c.post("/mcp", json=body, headers=headers)


async def test_calls_from_the_claude_app_and_the_voice_app_are_counted_as_voice_activity(bridge):
    server, _, store = bridge
    usage.record(store, "claude_app", "conn-1", "whats_new", 120, at=NOW)
    usage.record(store, "claude_app", "conn-1", "fleet_recap", 380, at=NOW + 90)
    usage.record(store, "voice_app", "trace-9", "ask_active_session", 200, at=NOW + 30)
    usage.record(store, "claude_app", "conn-2", "whats_new", 100, at=NOW - 86400)  # yesterday
    out = usage.voice(store, now=NOW + 100)
    today = out["today"]
    assert today["connections"] == 2 and today["tool_calls"] == 3 and today["chars_out"] == 700
    assert today["est_tokens"] == 175 and today["minutes"] == 2  # conn-1 spans 90 s, rounded up
    assert "estimate" in out["label"] and len(out["last_7_days"]) == 7


def test_who_a_call_is_from_is_read_from_where_it_came_from_and_its_trace():
    assert usage.client_of("160.79.106.128", None) == "claude_app"
    assert usage.client_of("127.0.0.1", "ab12cd34ef56") == "voice_app"
    assert usage.client_of("127.0.0.1", None) is None  # this computer's own tools are not voice
    assert usage.client_of("100.101.102.103", None) == "claude_app"


async def test_the_event_api_gives_the_plan_the_voice_estimate_and_each_sessions_context(bridge, tmp_path):
    _, app, store = bridge
    statusline(tmp_path / "statusline", "s1", NOW, context=40, limits=limits(30, 12))
    usage.record(store, "claude_app", "conn-1", "whats_new", 400, at=NOW)
    async with from_address(app, "127.0.0.1") as c:
        out = (await c.get("/api/v1/usage", headers=BEARER)).json()
    assert set(out) == {"plan", "warning", "voice"}
    assert out["voice"]["label"] == "estimate, voice activity via the bridge"
    assert "cost" not in json.dumps(out)


async def test_a_session_in_the_list_carries_its_context(tmp_path):
    live = tmp_path / "live"
    live.mkdir()
    (tmp_path / "home" / "app").mkdir(parents=True)
    (live / "x.json").write_text(
        json.dumps(
            {
                "pid": os.getpid(),
                "sessionId": "s1",
                "cwd": str(tmp_path / "home" / "app"),
                "name": "app-1",
                "status": "idle",
            }
        )
    )
    statusline(tmp_path / "statusline", "s1", NOW, context=55)
    m = SessionManager(Store(tmp_path / "b.db"), project_root=tmp_path / "home", client_factory=FakeClaude())
    server = build_server(
        m,
        conversations=lambda **k: [],
        live_dir=live,
        projects_dir=tmp_path / "p",
        statusline_dir=tmp_path / "statusline",
    )
    async with Client(server) as c:
        rows = (await c.call_tool("fleet_recap", {})).structured_content["sessions"]
    assert next(r for r in rows if r["name"] == "app-1")["context"]["used_percentage"] == 55


def test_a_call_from_the_voice_app_counts_the_owner_as_away(bridge):
    from types import SimpleNamespace

    from claude_voice import server as bridge_module

    request = SimpleNamespace(scope={"client": ("127.0.0.1", 4321), "headers": []}, headers={})
    seen = bridge_module.PRESENCE[0]
    assert seen.voice_at is None
    bridge_module.note_voice_call(SimpleNamespace(request=request), "whats_new", "ab12cd34ef56", None)
    assert seen.voice_at is not None
