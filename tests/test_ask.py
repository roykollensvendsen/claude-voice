"""Asking a session that is open elsewhere, and reading its answer as it comes."""

import asyncio
import json
import os

import pytest
from fakes import FakeClaude
from mcp.client import Client
from test_live import msg

from claude_voice.server import build_server
from claude_voice.sessions import SessionManager
from claude_voice.store import Store

PID = os.getpid()


@pytest.fixture
def root(tmp_path):
    (tmp_path / "src" / "billing").mkdir(parents=True)
    return tmp_path / "src"


class Target:
    """A running session: its registry file and its growing transcript."""

    def __init__(self, live, cwd, name="billing-ab", status="idle"):
        self.file = live / f"{PID}.json"
        self.name, self.cwd = name, cwd
        self.transcript = [
            msg("user", "earlier question"),
            msg("assistant", [{"type": "text", "text": "earlier answer"}]),
        ]
        self.set(status)

    def set(self, status):
        self.file.write_text(
            json.dumps({"pid": PID, "sessionId": "sid-1", "cwd": self.cwd, "name": self.name, "status": status})
        )

    def says(self, text):
        self.transcript.append(msg("assistant", [{"type": "text", "text": text}]))


def bridge(tmp_path, root, live, target, deliver, receive_seconds=15.0):
    m = SessionManager(Store(tmp_path / "b.db"), project_root=root, client_factory=FakeClaude())
    return build_server(
        m,
        conversations=lambda directory=None, limit=None: [],
        live_dir=live,
        deliver=deliver,
        read_transcript=lambda sid, directory: list(target.transcript),
        poll_seconds=0.01,
        receive_seconds=receive_seconds,
        projects_dir=tmp_path / "projects",
    )


def continued(tmp_path, cwd, sid, new_sid):
    """Write the raw transcript of a conversation that Claude Code continued elsewhere."""
    from claude_agent_sdk import project_key_for_directory

    path = tmp_path / "projects" / project_key_for_directory(cwd) / f"{sid}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "last words"}]}},
        {"type": "continued-in", "sessionId": sid, "continuedInSessionId": new_sid},
    ]
    path.write_text("".join(json.dumps(x) + "\n" for x in lines))


@pytest.fixture
def live(tmp_path):
    d = tmp_path / "live"
    d.mkdir()
    return d


async def call(client, name, **args):
    res = await client.call_tool(name, args)
    assert not res.is_error, res.content
    return res.structured_content


async def test_ask_waits_for_the_session_to_answer_and_returns_only_the_new_reply(tmp_path, root, live):
    target = Target(live, str(root / "billing"))

    async def deliver(name, text):
        async def work():
            await asyncio.sleep(0.05)
            target.set("busy")
            target.transcript.append(msg("user", text))
            target.says("Looking.")
            await asyncio.sleep(0.05)
            target.says("The export is fixed.")
            target.set("idle")

        asyncio.get_running_loop().create_task(work())
        return {"delivered": True, "detail": "DELIVERED"}

    async with Client(bridge(tmp_path, root, live, target, deliver)) as c:
        out = await call(c, "ask_active_session", session="billing-ab", message="Is the export fixed?", wait_seconds=5)
    assert out["status"] == "answered"
    assert out["session_ended"] is False
    assert out["reply"] == "Looking.\nThe export is fixed."
    assert [t["text"] for t in out["turns"]] == ["Looking.", "The export is fixed."]


async def test_a_session_still_working_when_the_wait_ends_gives_a_cursor(tmp_path, root, live):
    target = Target(live, str(root / "billing"))

    async def deliver(name, text):
        target.set("busy")
        target.says("Starting on it.")
        return {"delivered": True, "detail": "DELIVERED"}

    async with Client(bridge(tmp_path, root, live, target, deliver)) as c:
        out = await call(c, "ask_active_session", session="billing-ab", message="go", wait_seconds=0.2)
        assert out["status"] == "still_working"
        assert out["reply"] == "Starting on it."
        target.says("Done now.")
        later = await call(c, "read_session_output", session="billing-ab", after=out["next_after"])
    assert [t["text"] for t in later["turns"]] == ["Done now."]
    assert later["next_after"] == out["next_after"] + 1


async def test_a_session_that_exits_during_the_wait_is_reported_at_once(tmp_path, root, live):
    target = Target(live, str(root / "billing"))

    async def deliver(name, text):
        target.set("busy")
        target.file.unlink()
        return {"delivered": True, "detail": "DELIVERED"}

    async with Client(bridge(tmp_path, root, live, target, deliver)) as c:
        out = await call(c, "ask_active_session", session="billing-ab", message="go", wait_seconds=5)
    assert out["status"] == "session_ended"
    assert out["session_ended"] is True


async def test_couriers_cannot_be_asked_or_messaged(tmp_path, root, live):
    target = Target(live, str(root / "billing"), name="claude-voice-msg-ab12-cd")

    async def deliver(name, text):
        raise AssertionError("must not deliver")

    async with Client(bridge(tmp_path, root, live, target, deliver)) as c:
        asked = await c.call_tool("ask_active_session", {"session": "claude-voice-msg-ab12-cd", "message": "hi"})
        sent = await c.call_tool("message_active_session", {"session": "claude-voice-msg-ab12-cd", "message": "hi"})
    assert asked.is_error and sent.is_error


async def test_read_session_output_numbers_turns_so_a_reader_can_continue(tmp_path, root, live):
    target = Target(live, str(root / "billing"))
    async with Client(bridge(tmp_path, root, live, target, None)) as c:
        first = await call(c, "read_session_output", session="billing-ab")
    assert [t["index"] for t in first["turns"]] == [0, 1]
    assert first["next_after"] == 1


async def test_a_reply_too_quick_to_be_seen_busy_still_counts_as_answered(tmp_path, root, live):
    target = Target(live, str(root / "billing"))

    async def deliver(name, text):
        target.says("pong")  # the whole turn happened between two looks at the registry
        return {"delivered": True, "detail": "DELIVERED"}

    async with Client(bridge(tmp_path, root, live, target, deliver)) as c:
        out = await call(c, "ask_active_session", session="billing-ab", message="ping", wait_seconds=5)
    assert out["status"] == "answered"
    assert out["reply"] == "pong"


async def test_an_idle_session_with_no_reply_yet_is_waited_for(tmp_path, root, live):
    target = Target(live, str(root / "billing"))

    async def deliver(name, text):
        async def later():
            await asyncio.sleep(0.1)
            target.says("late pong")

        asyncio.get_running_loop().create_task(later())
        return {"delivered": True, "detail": "DELIVERED"}

    async with Client(bridge(tmp_path, root, live, target, deliver)) as c:
        out = await call(c, "ask_active_session", session="billing-ab", message="ping", wait_seconds=5)
    assert out["status"] == "answered"
    assert out["reply"] == "late pong"


async def test_a_message_that_never_arrives_is_reported_instead_of_waited_out(tmp_path, root, live):
    target = Target(live, str(root / "billing"))

    async def deliver(name, text):
        return {"delivered": True, "detail": "DELIVERED"}  # handed over, but never shows up

    async with Client(bridge(tmp_path, root, live, target, deliver, receive_seconds=0.1)) as c:
        out = await call(c, "ask_active_session", session="billing-ab", message="hello?", wait_seconds=30)
    assert out["status"] == "not_received"


async def test_a_session_continued_elsewhere_is_shown_as_moved_and_not_asked(tmp_path, root, live):
    target = Target(live, str(root / "billing"))  # sid-1
    continued(tmp_path, str(root / "billing"), "sid-1", "sid-2")

    async def deliver(name, text):
        raise AssertionError("a moved session must not be messaged")

    async with Client(bridge(tmp_path, root, live, target, deliver)) as c:
        listed = await call(c, "list_active_sessions")
        asked = await call(c, "ask_active_session", session="billing-ab", message="hi")
        sent = await call(c, "message_active_session", session="billing-ab", message="hi")
    row = listed["sessions"][0]
    assert row["status"] == "moved" and row["moved_to"] == {"id": "sid-2", "name": None}
    assert asked["status"] == "moved" and asked["moved_to"]["id"] == "sid-2"
    assert sent["status"] == "moved" and sent["delivered"] is False
