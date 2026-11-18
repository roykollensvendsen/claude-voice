"""The voice mailbox: a session's answer to the voice waits until the voice has read it out."""

import pytest
from mcp.client import Client
from test_events import call, server

from claude_voice import mailbox
from claude_voice.store import Store

HOUR = 3600.0


class Clock:
    def __init__(self) -> None:
        self.now = 1_000_000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def box(tmp_path, clock):
    return mailbox.Mailbox(Store(tmp_path / "b.db").db, clock=clock)


def senders(replies):
    return [r["session"] for r in replies]


def test_a_returned_reply_is_returned_again_until_it_is_acknowledged(box):
    seq = box.put("fuel-mcp", "Diesel is 21.90 today.")
    assert senders(box.push()) == ["fuel-mcp"]
    assert senders(box.push()) == ["fuel-mcp"]  # returned once, never confirmed: returned again
    box.ack(upto=seq)
    assert box.push() == []


def test_a_reply_is_read_only_once_a_later_call_acknowledges_it(box):
    first = box.put("a", "one")
    box.push()
    late = box.put("b", "two")  # arrived after that answer
    box.ack(upto=late)
    assert senders(box.push()) == ["b"]
    box.ack(seqs=[first])  # already read; an explicit ack of it changes nothing
    assert senders(box.push()) == ["b"]


def test_an_explicit_ack_marks_a_returned_reply_read(box):
    seq = box.put("a", "one")
    box.push()
    box.ack(seqs=[seq])
    assert box.push() == []


def test_after_three_pushes_a_reply_is_counted_not_pushed(box):
    box.put("a", "one")
    for _ in range(3):
        assert senders(box.push()) == ["a"]
    assert box.push() == []
    assert box.held() == {"not_confirmed": 1, "older": 0}


def test_an_unread_reply_older_than_a_day_is_counted_not_pushed(box, clock):
    box.put("a", "one")
    clock.now += 24 * HOUR + 1
    assert box.push() == []
    assert box.held() == {"not_confirmed": 0, "older": 1}


def test_at_most_five_replies_per_answer_newest_last_each_clipped(box, clock):
    for n in range(7):
        box.put(f"s{n}", "x" * 900)
        clock.now += 1
    replies = box.push()
    assert senders(replies) == ["s2", "s3", "s4", "s5", "s6"]
    assert all(len(r["text"]) <= len("Reply from s6: ") + 600 for r in replies)
    assert replies[-1]["age"] == "1 s ago"


def test_listing_the_replies_shows_the_held_ones_too(box, clock):
    box.put("old", "from yesterday")
    clock.now += 25 * HOUR
    box.put("new", "just now")
    for _ in range(3):
        box.push()
    assert senders(box.unread()) == ["old", "new"]


def test_only_read_replies_are_purged_after_seven_days(box, clock):
    read = box.put("a", "read")
    box.put("b", "never read")
    box.push()
    box.ack(seqs=[read])
    clock.now += 7 * 24 * HOUR + 1
    box.put("c", "new")  # adding a reply purges
    assert senders(box.unread()) == ["b", "c"]
    assert box.db.execute("SELECT COUNT(*) FROM mailbox").fetchone()[0] == 2


# -- through the voice's tools -------------------------------------------------------


async def test_whats_new_carries_unread_replies_even_without_a_cursor(tmp_path, monkeypatch):
    from claude_voice import transcripts

    monkeypatch.setattr(transcripts._courier, "on_reply", None)
    store, live, root = Store(tmp_path / "b.db"), tmp_path / "live", tmp_path / "src"
    live.mkdir()
    root.mkdir()
    _, _, srv = server(store, live, root)
    async with Client(srv) as c:
        transcripts._courier.on_reply("fuel-mcp", "Diesel is 21.90 today.")
        first = await call(c, "whats_new")  # a fresh connection: no cursor
        assert [(e["session"], e["kind"]) for e in first["events"]] == [("fuel-mcp", "reply")]
        assert first["events"][0]["text"] == "Reply from fuel-mcp: Diesel is 21.90 today."
        again = await call(c, "whats_new")  # the connection dropped before it was read out
        assert [e["kind"] for e in again["events"]] == ["reply"]
        later = await call(c, "whats_new", cursor=again["cursor"])
        assert later["events"] == []


async def test_whats_new_hands_over_each_reply_once(tmp_path, monkeypatch):
    """The feed keeps a copy of each reply for the phone page; the voice gets it from the mailbox
    alone, so a call with a cursor never hears it twice."""
    from claude_voice import transcripts

    monkeypatch.setattr(transcripts._courier, "on_reply", None)
    store, live, root = Store(tmp_path / "b.db"), tmp_path / "live", tmp_path / "src"
    live.mkdir()
    root.mkdir()
    _, _, srv = server(store, live, root)
    async with Client(srv) as c:
        first = await call(c, "whats_new")
        transcripts._courier.on_reply("fuel-mcp", "Diesel is 21.90 today.")
        out = await call(c, "whats_new", cursor=first["cursor"])
    assert [(e["session"], e["kind"]) for e in out["events"]] == [("fuel-mcp", "reply")]


async def test_whats_new_says_when_replies_wait_unheard(tmp_path, monkeypatch):
    from claude_voice import transcripts

    monkeypatch.setattr(transcripts._courier, "on_reply", None)
    store, live, root = Store(tmp_path / "b.db"), tmp_path / "live", tmp_path / "src"
    live.mkdir()
    root.mkdir()
    _, _, srv = server(store, live, root)
    async with Client(srv) as c:
        transcripts._courier.on_reply("fuel-mcp", "Diesel is 21.90 today.")
        for _ in range(3):
            await call(c, "whats_new")
        out = await call(c, "whats_new")
        assert out["events"] == []
        assert out["replies_waiting"] == "1 reply not confirmed heard; list_replies reads it."
        listed = await call(c, "list_replies")
        assert [r["session"] for r in listed["replies"]] == ["fuel-mcp"]
        await call(c, "whats_new", ack=[listed["replies"][0]["reply_id"]])
        assert "replies_waiting" not in await call(c, "whats_new")
