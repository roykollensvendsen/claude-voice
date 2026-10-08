"""health(): the bridge's own state, cheap enough to poll."""

from fakes import FakeClaude
from mcp.client import Client

from claude_voice.metrics import METRICS
from claude_voice.server import build_server
from claude_voice.sessions import SessionManager
from claude_voice.store import Store


def server(tmp_path, **extra):
    (tmp_path / "src").mkdir(exist_ok=True)
    m = SessionManager(Store(tmp_path / "b.db"), project_root=tmp_path / "src", client_factory=FakeClaude())
    return build_server(m, conversations=lambda **k: [], live_dir=tmp_path / "live", **extra)


async def test_health_reports_the_bridges_state_and_its_calls(tmp_path):
    METRICS.reset()
    srv = server(tmp_path, restart={"at": "2026-10-08T10:00:00+00:00", "reason": "auto-restart", "total": 2})
    async with Client(srv) as c:
        await c.call_tool("whats_new", {})
        await c.call_tool("session_recap", {"session_id": "nope"}, meta={"trace_id": "t9"})
        h = (await c.call_tool("health", {})).structured_content
    now = h["now"]
    assert now["up_since"] and now["version"] and now["memory_rss_mb"] > 0
    assert now["last_restart"] == {"at": "2026-10-08T10:00:00+00:00", "reason": "auto-restart"}
    assert now["restarts_total"] == 2
    tools = {t["name"]: t for t in h["tools"]}
    assert tools["whats_new"]["calls"] == 1
    assert h["errors"][0]["trace_id"] == "t9" and h["errors"][0]["kind"] == "tool_error"
    assert h["courier"] == {"warm": False, "last_delivery_ms": None}
    assert h["watcher"]["last_check_at"] and h["watcher"]["tree_version"]


async def test_without_restart_knowledge_the_reason_is_unknown(tmp_path):
    async with Client(server(tmp_path)) as c:
        h = (await c.call_tool("health", {})).structured_content
    assert h["now"]["last_restart"]["reason"] is None
    assert h["watcher"]["last_check_at"] is None  # nothing has been checked yet
