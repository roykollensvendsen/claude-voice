"""Talking to Claude Code sessions that are open elsewhere (e.g. in a terminal)."""

import json
import os
from types import SimpleNamespace

import pytest
from fakes import FakeClaude, result
from mcp.client import Client

from claude_voice.server import build_server
from claude_voice.sessions import SessionManager
from claude_voice.store import Store
from claude_voice.transcripts import recent_turns


def msg(type_, content):
    return SimpleNamespace(type=type_, message={"role": type_, "content": content})


TRANSCRIPT = [
    msg("user", "Fix the invoice export"),
    msg("assistant", [{"type": "thinking", "thinking": "hmm"}]),
    msg("assistant", [{"type": "text", "text": "Looking at export.py."}]),
    msg("assistant", [{"type": "tool_use", "id": "t1", "name": "Read", "input": {}}]),
    msg("user", [{"type": "tool_result", "tool_use_id": "t1", "content": "..."}]),
    msg("assistant", [{"type": "text", "text": "Fixed; totals now include VAT."}]),
    msg("user", [{"type": "text", "text": "Run the tests too"}]),
    msg("assistant", [{"type": "text", "text": "All 12 tests pass."}]),
]


def test_recent_turns_keeps_what_a_person_would_read():
    turns = recent_turns(TRANSCRIPT, limit=10)
    assert turns == [
        {"role": "user", "text": "Fix the invoice export"},
        {"role": "assistant", "text": "Looking at export.py.", "tools": []},
        {"role": "assistant", "text": "Fixed; totals now include VAT.", "tools": ["Read"]},
        {"role": "user", "text": "Run the tests too"},
        {"role": "assistant", "text": "All 12 tests pass.", "tools": []},
    ]


def test_recent_turns_limit_keeps_the_newest():
    assert [t["text"] for t in recent_turns(TRANSCRIPT, limit=2)] == [
        "Run the tests too",
        "All 12 tests pass.",
    ]


# -- through the MCP tools ------------------------------------------------------


@pytest.fixture
def root(tmp_path):
    (tmp_path / "src" / "billing").mkdir(parents=True)
    return tmp_path / "src"


@pytest.fixture
def live(tmp_path, root):
    d = tmp_path / "live"
    d.mkdir()
    (d / f"{os.getpid()}.json").write_text(
        json.dumps(
            {
                "pid": os.getpid(),
                "sessionId": "current-id",
                "cwd": str(root / "billing"),
                "name": "billing-ab",
                "status": "idle",
                "updatedAt": 0,
            }
        )
    )
    return d


class Recorder:
    def __init__(self, ok=True):
        self.sent = []
        self.ok = ok

    async def __call__(self, name, text):
        self.sent.append((name, text))
        return {"delivered": self.ok, "detail": "queued" if self.ok else "no such session"}


def server_for(tmp_path, root, live, deliver=None, read=None, claude=None):
    m = SessionManager(
        Store(tmp_path / "b.db"), project_root=root, client_factory=claude or FakeClaude()
    )
    srv = build_server(
        m,
        conversations=lambda directory=None, limit=None: [],
        live_dir=live,
        deliver=deliver or Recorder(),
        read_transcript=read or (lambda sid, directory: TRANSCRIPT),
    )
    return m, srv


async def call(client, name, **args):
    res = await client.call_tool(name, args)
    assert not res.is_error, res.content
    return res.structured_content


async def test_message_goes_to_the_open_session_by_name(tmp_path, root, live):
    deliver = Recorder()
    _, srv = server_for(tmp_path, root, live, deliver=deliver)
    async with Client(srv) as c:
        out = await call(c, "message_active_session", session="billing-ab", message="status?")
    assert deliver.sent == [("billing-ab", "status?")]
    assert out["delivered"] is True


async def test_message_to_an_unknown_session_is_refused(tmp_path, root, live):
    deliver = Recorder()
    _, srv = server_for(tmp_path, root, live, deliver=deliver)
    async with Client(srv) as c:
        res = await c.call_tool("message_active_session", {"session": "nope", "message": "hi"})
    assert res.is_error and "list_active_sessions" in res.content[0].text
    assert deliver.sent == []


async def test_read_session_output_uses_the_current_transcript(tmp_path, root, live):
    seen = []

    def read(sid, directory):
        seen.append((sid, directory))
        return TRANSCRIPT

    _, srv = server_for(tmp_path, root, live, read=read)
    async with Client(srv) as c:
        out = await call(c, "read_session_output", session="billing-ab", limit=2)
    assert seen == [("current-id", str(root / "billing"))]
    assert out["turns"][-1]["text"] == "All 12 tests pass."
    assert out["status"] == "idle"


async def test_attaching_backfills_recent_history(tmp_path, root, live):
    m, srv = server_for(tmp_path, root, live)
    async with Client(srv) as c:
        s = await call(c, "attach_conversation", claude_session_id="current-id")
        recap = await call(c, "session_recap", session_id=s["id"])
        msgs = await call(c, "get_messages", session_id=s["id"])
    assert recap["latest_text"] == "All 12 tests pass."
    assert recap["last_prompt"] == "Run the tests too"
    assert "history" in {e["kind"] for e in msgs["events"]}


async def test_a_turn_that_does_nothing_is_an_error(tmp_path, root, live):
    claude = FakeClaude([result("", "c-1", num_turns=0)])
    m, srv = server_for(tmp_path, root, live, claude=claude)
    async with Client(srv) as c:
        s = await call(c, "create_session", project="billing")
        await call(c, "send_task", session_id=s["id"], prompt="go")
        await m.wait(s["id"])
        recap = await call(c, "session_recap", session_id=s["id"])
    assert recap["status"] == "error"
    assert "open in another" in recap["last_error"]


# -- searching a long transcript ---------------------------------------------------

from claude_voice.transcripts import search_turns  # noqa: E402

LONG = [
    msg("user", "Set up the OAuth login for ChatGPT"),
    msg("assistant", [{"type": "text", "text": "OAuth works; tokens are stored hashed."}]),
    msg("user", "Now move Funnel to port 443"),
    msg("assistant", [{"type": "text", "text": "Funnel is on 443; port 10000 is closed."}]),
    msg("user", "What about the invoice export?"),
    msg(
        "assistant",
        [{"type": "text", "text": "x" * 500 + " The invoice export rounds VAT down. " + "y" * 500}],
    ),
]


def test_search_returns_only_matching_turns_best_first():
    hits = search_turns(recent_turns(LONG, limit=None), "funnel 443", limit=5)
    assert [h["turn"] for h in hits] == [3, 2]
    assert hits[0]["text"] == "Funnel is on 443; port 10000 is closed."
    assert hits[0]["role"] == "assistant"


def test_search_is_case_insensitive_and_ignores_unmatched_words():
    hits = search_turns(recent_turns(LONG, limit=None), "OAUTH banana", limit=5)
    assert [h["turn"] for h in hits] == [0, 1]


def test_long_turns_are_cut_to_a_snippet_around_the_match():
    hits = search_turns(recent_turns(LONG, limit=None), "VAT", limit=1, context_chars=40)
    snippet = hits[0]["text"]
    assert "rounds VAT down" in snippet
    assert len(snippet) < 140
    assert snippet.startswith("…") and snippet.endswith("…")


def test_no_match_gives_nothing():
    assert search_turns(recent_turns(LONG, limit=None), "kubernetes", limit=5) == []


async def test_search_tool_reads_the_whole_transcript(tmp_path, root, live):
    _, srv = server_for(tmp_path, root, live, read=lambda sid, directory: LONG)
    async with Client(srv) as c:
        out = await call(c, "search_session_history", session="billing-ab", query="invoice")
    assert out["total_turns"] == 6
    assert [h["turn"] for h in out["matches"]] == [4, 5] or [h["turn"] for h in out["matches"]] == [
        5,
        4,
    ]
