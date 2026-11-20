"""A session's unheard answers come with the tools that wait on it, ask it or sum it up."""

import pytest
from mcp.client import Client
from test_ask import Target, bridge, call
from test_mailbox import Clock

from claude_voice import mailbox, transcripts
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


@pytest.fixture
def courier(monkeypatch):
    """Put the courier's listener back after the test; the bridge under test sets its own."""
    monkeypatch.setattr(transcripts._courier, "on_reply", None)


def senders(items):
    return [r["session"] for r in items]


def test_a_session_s_replies_can_be_pushed_alone(tmp_path):
    box = mailbox.Mailbox(Store(tmp_path / "b.db").db, clock=Clock())
    box.put("billing-ab", "The export is fixed.")
    box.put("fuel-mcp", "Diesel is 21.90 today.")
    assert senders(box.push(sender="billing-ab")[1]) == ["billing-ab"]
    assert senders(box.push()[1]) == ["billing-ab", "fuel-mcp"]  # the other was not handed over


async def test_waiting_on_a_session_brings_its_unheard_replies_and_no_one_else_s(tmp_path, root, live, courier):
    target = Target(live, str(root / "billing"))
    async with Client(bridge(tmp_path, root, live, target, None)) as c:
        transcripts._courier.on_reply("billing-ab", "The export is fixed.")
        transcripts._courier.on_reply("fuel-mcp", "Diesel is 21.90 today.")
        out = await call(c, "wait_for_session", session="billing-ab", wait_seconds=1)
        news = await call(c, "whats_new")
    assert senders(out["replies"]) == ["billing-ab"]
    assert out["replies"][0]["text"] == "Reply from billing-ab: The export is fixed."
    assert "fuel-mcp" in senders(news["events"])  # still waiting for the voice


async def test_a_reply_a_wait_handed_over_is_confirmed_by_its_number(tmp_path, root, live, courier):
    """No cursor carries a wait's answer, so the voice confirms what it read with whats_new's ack."""
    target = Target(live, str(root / "billing"))
    async with Client(bridge(tmp_path, root, live, target, None)) as c:
        transcripts._courier.on_reply("billing-ab", "The export is fixed.")
        out = await call(c, "wait_for_session", session="billing-ab", wait_seconds=1)
        first = await call(c, "whats_new", ack=[out["replies"][0]["reply_id"]])
        later = await call(c, "whats_new")
    assert "billing-ab" not in senders(first["events"]) + senders(later["events"])


async def test_asking_a_session_brings_its_unheard_replies(tmp_path, root, live, courier):
    target = Target(live, str(root / "billing"))

    async def deliver(name, text):
        target.says("On it.")
        return {"delivered": True, "detail": "DELIVERED"}

    async with Client(bridge(tmp_path, root, live, target, deliver)) as c:
        transcripts._courier.on_reply("billing-ab", "The export is fixed.")
        out = await call(c, "ask_active_session", session="billing-ab", message="And the import?", wait_seconds=1)
    assert senders(out["replies"]) == ["billing-ab"]


async def test_a_recap_brings_the_session_s_unheard_replies(tmp_path, root, live, courier):
    target = Target(live, str(root / "billing"))
    srv = bridge(tmp_path, root, live, target, None)
    async with Client(srv) as c:
        made = await call(c, "create_session", project="billing", label="billing-ab")
        transcripts._courier.on_reply("billing-ab", "The export is fixed.")
        out = await call(c, "session_recap", session_id=made["id"])
    assert senders(out["replies"]) == ["billing-ab"]


async def test_an_ack_on_a_session_tool_marks_its_replies_heard(tmp_path, root, live, courier):
    """A voice that loops on wait_for_session confirms in the call it is already making."""
    target = Target(live, str(root / "billing"))
    async with Client(bridge(tmp_path, root, live, target, None)) as c:
        transcripts._courier.on_reply("billing-ab", "The export is fixed.")
        first = await call(c, "wait_for_session", session="billing-ab", wait_seconds=1)
        again = await call(
            c, "wait_for_session", session="billing-ab", wait_seconds=1, ack=[first["replies"][0]["reply_id"]]
        )
        news = await call(c, "whats_new")
    assert "replies" not in again
    assert "billing-ab" not in senders(news["events"])


@pytest.mark.parametrize("tool", ["ask_active_session", "session_recap"])
async def test_the_other_session_tools_take_an_ack_too(tmp_path, root, live, courier, tool):
    target = Target(live, str(root / "billing"))

    async def deliver(name, text):
        target.says("On it.")
        return {"delivered": True, "detail": "DELIVERED"}

    async with Client(bridge(tmp_path, root, live, target, deliver)) as c:
        made = await call(c, "create_session", project="billing", label="billing-ab")
        transcripts._courier.on_reply("billing-ab", "The export is fixed.")
        first = await call(c, "wait_for_session", session="billing-ab", wait_seconds=1)
        ack = [first["replies"][0]["reply_id"]]
        if tool == "ask_active_session":
            await call(c, tool, session="billing-ab", message="And the import?", wait_seconds=1, ack=ack)
        else:
            await call(c, tool, session_id=made["id"], ack=ack)
        news = await call(c, "whats_new")
    assert "billing-ab" not in senders(news["events"])


async def test_an_ack_with_an_unknown_or_read_id_is_ignored(tmp_path, root, live, courier):
    target = Target(live, str(root / "billing"))
    async with Client(bridge(tmp_path, root, live, target, None)) as c:
        transcripts._courier.on_reply("billing-ab", "The export is fixed.")
        out = await call(c, "wait_for_session", session="billing-ab", wait_seconds=1, ack=[9999])
        heard = out["replies"][0]["reply_id"]
        await call(c, "wait_for_session", session="billing-ab", wait_seconds=1, ack=[heard])
        transcripts._courier.on_reply("billing-ab", "And the import too.")
        again = await call(c, "wait_for_session", session="billing-ab", wait_seconds=1, ack=[heard])
    assert senders(out["replies"]) == ["billing-ab"]
    assert [r["text"] for r in again["replies"]] == ["Reply from billing-ab: And the import too."]


async def test_an_ack_marks_only_the_replies_it_names(tmp_path, root, live, courier):
    """Another session's reply stays unheard unless its own id is given."""
    target = Target(live, str(root / "billing"))
    async with Client(bridge(tmp_path, root, live, target, None)) as c:
        transcripts._courier.on_reply("billing-ab", "The export is fixed.")
        transcripts._courier.on_reply("fuel-mcp", "Diesel is 21.90 today.")
        seen = await call(c, "whats_new")
        billing = [e["reply_id"] for e in seen["events"] if e["session"] == "billing-ab"]
        await call(c, "wait_for_session", session="billing-ab", wait_seconds=1, ack=billing)
        news = await call(c, "whats_new")
    assert senders(news["events"]) == ["fuel-mcp"]


async def test_an_ack_on_one_sessions_tool_confirms_a_reply_from_any_session(tmp_path, root, live, courier):
    """The ids are the voice's own, so any reply it read out can be confirmed on any session tool."""
    target = Target(live, str(root / "billing"))
    async with Client(bridge(tmp_path, root, live, target, None)) as c:
        transcripts._courier.on_reply("fuel-mcp", "Diesel is 21.90 today.")
        seen = await call(c, "whats_new")
        fuel = [e["reply_id"] for e in seen["events"] if e["session"] == "fuel-mcp"]
        assert fuel  # an empty ack would leave nothing to confirm, and pass for the wrong reason
        await call(c, "wait_for_session", session="billing-ab", wait_seconds=1, ack=fuel)
        news = await call(c, "whats_new")
    assert senders(news["events"]) == []


async def test_a_reply_from_a_session_under_an_older_name_still_reaches_whats_new(tmp_path, root, live, courier):
    """The session tools match the reply's sender by name; whats_new still hands over the rest."""
    target = Target(live, str(root / "billing"))
    async with Client(bridge(tmp_path, root, live, target, None)) as c:
        transcripts._courier.on_reply("billing-old", "The export is fixed.")
        out = await call(c, "wait_for_session", session="billing-ab", wait_seconds=1)
        news = await call(c, "whats_new")
    assert "replies" not in out
    assert senders(news["events"]) == ["billing-old"]


async def test_without_unheard_replies_the_answer_has_no_replies_field(tmp_path, root, live, courier):
    target = Target(live, str(root / "billing"))
    async with Client(bridge(tmp_path, root, live, target, None)) as c:
        out = await call(c, "wait_for_session", session="billing-ab", wait_seconds=1)
    assert "replies" not in out
