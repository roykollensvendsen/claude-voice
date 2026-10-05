"""Runs against the logged-in Claude Code on this machine. Uses a little quota.

uv run pytest -m e2e
"""

import asyncio
import os

import pytest

from claude_voice.approvals import ApprovalBroker
from claude_voice.sessions import SessionManager
from claude_voice.store import Store

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(bool(os.environ.get("ANTHROPIC_API_KEY")), reason="would bill the API, not the subscription"),
]


async def test_a_real_turn_and_a_resumed_follow_up(tmp_path):
    (tmp_path / "proj").mkdir()
    store = Store(tmp_path / "bridge.db")
    m = SessionManager(store, project_root=tmp_path, approvals=ApprovalBroker(store))
    s = m.create("proj")

    await m.send(s["id"], "Reply with exactly the word pong and nothing else. Use no tools.")
    await asyncio.wait_for(m.wait(s["id"]), 180)
    first = m.recap(s["id"])
    assert first["status"] == "idle", first
    assert "pong" in first["last_result"].lower()
    claude_id = store.get_session(s["id"])["claude_session_id"]
    assert claude_id

    await m.send(s["id"], "What single word did you reply with last time? Answer with just it.")
    await asyncio.wait_for(m.wait(s["id"]), 180)
    second = m.recap(s["id"])
    assert "pong" in second["last_result"].lower(), second
    assert store.get_session(s["id"])["claude_session_id"] == claude_id


async def test_a_file_write_waits_for_approval(tmp_path):
    (tmp_path / "proj").mkdir()
    store = Store(tmp_path / "bridge.db")
    broker = ApprovalBroker(store, timeout_seconds=120)
    m = SessionManager(store, project_root=tmp_path, approvals=broker)
    s = m.create("proj")

    await m.send(s["id"], "Create a file hello.txt containing the word hi, using the Write tool.")
    for _ in range(1200):
        if broker.pending(s["id"]) or not m.is_running(s["id"]):
            break
        await asyncio.sleep(0.1)
    [req] = broker.pending(s["id"])
    assert req["tool"] == "Write"
    assert not (tmp_path / "proj" / "hello.txt").exists()

    broker.approve(req["id"])
    await asyncio.wait_for(m.wait(s["id"]), 180)
    assert (tmp_path / "proj" / "hello.txt").read_text().strip() == "hi"
