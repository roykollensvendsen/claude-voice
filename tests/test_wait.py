"""Waiting for a running session's turn to end, without sending it anything."""

import asyncio
import time

import pytest
from fakes import FakeClaude
from mcp.client import Client
from test_ask import Target, bridge, call, continued
from test_live import msg

from claude_voice import config
from claude_voice.server import build_server
from claude_voice.sessions import SessionManager
from claude_voice.store import Store


@pytest.fixture
def root(tmp_path):
    (tmp_path / "src" / "billing").mkdir(parents=True)
    return tmp_path / "src"


@pytest.fixture
def live(tmp_path):
    d = tmp_path / "live"
    d.mkdir()
    return d


async def test_waiting_returns_once_the_turn_has_ended(tmp_path, root, live):
    target = Target(live, str(root / "billing"), status="busy")

    async def work():
        await asyncio.sleep(0.05)
        target.says("Done: the export is fixed.")
        target.set("idle")

    async with Client(bridge(tmp_path, root, live, target, None)) as c:
        asyncio.get_running_loop().create_task(work())
        out = await call(c, "wait_for_session", session="billing-ab", wait_seconds=5)
    assert out["status"] == "idle"
    assert out["reply"] == "Done: the export is fixed."
    assert out["reply_final"] is True
    assert out["next_after"] == 2


async def test_with_after_the_wait_is_for_a_reply_past_that_turn(tmp_path, root, live):
    # Seen live: a message is delivered, the session has not picked it up yet, so it looks
    # idle; the caller must not be told its turn has ended before it has even begun.
    target = Target(live, str(root / "billing"))

    async def work():
        await asyncio.sleep(0.05)
        target.set("busy")
        target.transcript.append(msg("user", "ping"))
        target.says("pong")
        target.set("idle")

    async with Client(bridge(tmp_path, root, live, target, None)) as c:
        asyncio.get_running_loop().create_task(work())
        out = await call(c, "wait_for_session", session="billing-ab", after=1, wait_seconds=5)
    assert out["status"] == "idle"
    assert [t["text"] for t in out["turns"]] == ["pong"]
    assert out["reply"] == "pong" and out["next_after"] == 3


async def test_an_idle_session_with_nothing_asked_of_it_returns_at_once(tmp_path, root, live):
    target = Target(live, str(root / "billing"))
    async with Client(bridge(tmp_path, root, live, target, None)) as c:
        started = time.monotonic()
        out = await call(c, "wait_for_session", session="billing-ab", wait_seconds=5)
    assert time.monotonic() - started < 1
    # Reviewed upstream: without `after`, only what is written during the wait comes back, so an
    # answer from before it is never handed over as if it were the reply.
    assert out["status"] == "idle" and out["reply"] == "" and out["reply_final"] is False


async def test_a_turn_ending_on_tool_calls_alone_hands_back_no_earlier_answer(tmp_path, root, live):
    # Reviewed upstream: the session's last words were from the turn before, marked final.
    target = Target(live, str(root / "billing"), status="busy")

    async def work():
        await asyncio.sleep(0.05)
        target.transcript.append(msg("user", "run the tests"))
        target.transcript.append(msg("assistant", [{"type": "tool_use", "name": "Bash"}]))
        target.set("idle")

    async with Client(bridge(tmp_path, root, live, target, None)) as c:
        asyncio.get_running_loop().create_task(work())
        out = await call(c, "wait_for_session", session="billing-ab", wait_seconds=5)
    assert out["status"] == "idle"
    assert out["reply"] == "" and out["reply_final"] is False and out["turns"] == []


async def test_with_after_a_turn_that_wrote_no_text_ends_the_wait(tmp_path, root, live):
    # Reviewed upstream: the message was taken and the turn ended, but with nothing written past
    # `after` the wait ran to its deadline and said still_working.
    target = Target(live, str(root / "billing"))
    target.transcript.append(msg("user", "run the tests"))
    target.transcript.append(msg("assistant", [{"type": "tool_use", "name": "Bash"}]))
    async with Client(bridge(tmp_path, root, live, target, None)) as c:
        started = time.monotonic()
        out = await call(c, "wait_for_session", session="billing-ab", after=1, wait_seconds=5)
    assert time.monotonic() - started < 2
    assert out["status"] == "idle" and out["reply"] == "" and out["reply_final"] is False


async def test_with_after_a_message_not_taken_up_yet_is_still_waited_for(tmp_path, root, live):
    target = Target(live, str(root / "billing"))
    async with Client(bridge(tmp_path, root, live, target, None)) as c:
        out = await call(c, "wait_for_session", session="billing-ab", after=1, wait_seconds=0.3)
    assert out["status"] == "still_working"


async def test_a_turn_still_going_when_the_wait_ends_says_so_with_the_latest_words(tmp_path, root, live):
    target = Target(live, str(root / "billing"), status="busy")
    target.says("Starting on it.")
    async with Client(bridge(tmp_path, root, live, target, None)) as c:
        out = await call(c, "wait_for_session", session="billing-ab", wait_seconds=0.2)
    assert out["status"] == "still_working"
    assert out["reply"] == "Starting on it." and out["reply_final"] is False


async def test_a_session_that_needs_input_ends_the_wait(tmp_path, root, live):
    target = Target(live, str(root / "billing"), status="busy")

    async def work():
        await asyncio.sleep(0.05)
        target.set("waiting")

    async with Client(bridge(tmp_path, root, live, target, None)) as c:
        asyncio.get_running_loop().create_task(work())
        out = await call(c, "wait_for_session", session="billing-ab", wait_seconds=5)
    assert out["status"] == "needs_input"


async def test_a_session_that_exits_during_the_wait_is_reported(tmp_path, root, live):
    target = Target(live, str(root / "billing"), status="busy")

    async def work():
        await asyncio.sleep(0.05)
        target.file.unlink()

    async with Client(bridge(tmp_path, root, live, target, None)) as c:
        asyncio.get_running_loop().create_task(work())
        out = await call(c, "wait_for_session", session="billing-ab", wait_seconds=5)
    assert out["status"] == "session_ended" and out["session_ended"] is True


async def test_a_session_continued_elsewhere_is_shown_as_moved(tmp_path, root, live):
    target = Target(live, str(root / "billing"))
    continued(tmp_path, str(root / "billing"), "sid-1", "sid-2")
    async with Client(bridge(tmp_path, root, live, target, None)) as c:
        out = await call(c, "wait_for_session", session="billing-ab")
    assert out["status"] == "moved" and out["moved_to"]["id"] == "sid-2"


async def test_a_wait_for_a_session_stays_within_the_callers_time_budget(tmp_path, root, live):
    target = Target(live, str(root / "billing"), status="busy")
    m = SessionManager(Store(tmp_path / "b.db"), project_root=root, client_factory=FakeClaude())
    repos = config.Config(
        config.Repos(), config.Paths(None, None, None, None), None, budgets=config.Budgets({"default": 0.5})
    )
    server = build_server(
        m,
        conversations=lambda directory=None, limit=None: [],
        live_dir=live,
        read_transcript=lambda sid, directory: list(target.transcript),
        poll_seconds=0.01,
        projects_dir=tmp_path / "projects",
        repo_config=repos,
    )
    async with Client(server) as c:
        started = time.monotonic()
        out = await call(c, "wait_for_session", session="billing-ab", wait_seconds=60)
    assert out["status"] == "still_working" and time.monotonic() - started < 5


async def test_after_a_long_stretch_the_turns_are_capped_and_the_newest_reply_shown(tmp_path, root, live):
    # Reviewed upstream: every turn past `after` came back, a lot to read aloud after long work.
    target = Target(live, str(root / "billing"))
    for i in range(35):
        target.transcript.append(msg("user", f"q{i}"))
        target.says(f"a{i}")
    async with Client(bridge(tmp_path, root, live, target, None)) as c:
        out = await call(c, "wait_for_session", session="billing-ab", after=1, wait_seconds=5)
    assert len(out["turns"]) == 30 and out["turns_left"] == 5
    assert out["turns"][0]["text"] == "a0" and out["next_after"] == out["turns"][-1]["index"]
    assert out["latest"]["text"] == "a34" and out["reply"] == "a34"


async def test_a_session_that_ends_during_the_wait_keeps_the_words_it_wrote(tmp_path, root, live):
    target = Target(live, str(root / "billing"), status="busy")

    async def work():
        await asyncio.sleep(0.05)
        target.says("Halfway through the export.")
        target.file.unlink()

    async with Client(bridge(tmp_path, root, live, target, None)) as c:
        asyncio.get_running_loop().create_task(work())
        out = await call(c, "wait_for_session", session="billing-ab", after=1, wait_seconds=5)
    assert out["status"] == "session_ended"
    assert out["reply"] == "Halfway through the export." and out["reply_final"] is False
