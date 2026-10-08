"""digest_session: a long conversation summed up outside the caller's context."""

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
def setup(tmp_path):
    (tmp_path / "src" / "billing").mkdir(parents=True)
    live = tmp_path / "live"
    live.mkdir()
    (live / "x.json").write_text(
        json.dumps({"pid": PID, "sessionId": "sid-1", "cwd": str(tmp_path / "src" / "billing"), "name": "billing-ab"})
    )
    return tmp_path


class Summarizer:
    def __init__(self, answer="The export was fixed and the tests pass."):
        self.answer = answer
        self.calls = []
        self.languages = []

    async def __call__(self, text, question, language=None):
        self.calls.append((text, question))
        self.languages.append(language)
        return self.answer


def bridge(tmp_path, turns, summarize, digest_chars=150_000):
    m = SessionManager(Store(tmp_path / "b.db"), project_root=tmp_path / "src", client_factory=FakeClaude())
    return build_server(
        m,
        conversations=lambda **k: [],
        live_dir=tmp_path / "live",
        read_transcript=lambda sid, directory: turns,
        summarize=summarize,
        digest_chars=digest_chars,
    )


TURNS = [
    msg("user", "Fix the export"),
    msg("assistant", [{"type": "tool_use", "id": "t", "name": "Bash", "input": {}}]),
    msg("assistant", [{"type": "text", "text": "Fixed; tests pass."}]),
]


async def test_the_conversation_is_summed_up_by_someone_else_and_only_the_digest_returns(setup):
    summarize = Summarizer()
    async with Client(bridge(setup, TURNS, summarize)) as c:
        out = (await c.call_tool("digest_session", {"session": "billing-ab"})).structured_content
    assert out["digest"] == "The export was fixed and the tests pass."
    assert out["cut"] is False and out["skipped_turns"] == 0
    text, question = summarize.calls[0]
    assert "Fix the export" in text and "Fixed; tests pass." in text and "Bash" in text
    assert question is None


async def test_a_question_is_passed_on(setup):
    summarize = Summarizer("Yes, all 12 passed.")
    async with Client(bridge(setup, TURNS, summarize)) as c:
        out = (
            await c.call_tool("digest_session", {"session": "billing-ab", "question": "Did the tests pass?"})
        ).structured_content
    assert out["digest"] == "Yes, all 12 passed."
    assert summarize.calls[0][1] == "Did the tests pass?"


async def test_a_long_conversation_keeps_its_end_and_says_how_much_was_left_out(setup):
    turns = [msg("user", f"old message {i} " + "x" * 100) for i in range(50)] + TURNS
    summarize = Summarizer()
    async with Client(bridge(setup, turns, summarize, digest_chars=500)) as c:
        out = (await c.call_tool("digest_session", {"session": "billing-ab"})).structured_content
    assert out["cut"] is True
    assert out["skipped_turns"] > 0 and out["skipped_chars"] > 0
    assert "Fixed; tests pass." in summarize.calls[0][0]
    assert "old message 0 " not in summarize.calls[0][0]


async def test_the_digest_is_kept_short_enough_to_say(setup):
    async with Client(bridge(setup, TURNS, Summarizer("word " * 400))) as c:
        out = (await c.call_tool("digest_session", {"session": "billing-ab"})).structured_content
    assert len(out["digest"]) <= 600


async def test_a_long_digest_is_cut_after_its_last_whole_sentence(setup):
    long = " ".join(f"Sentence number {i} says something useful." for i in range(30))
    async with Client(bridge(setup, TURNS, Summarizer(long))) as c:
        out = (await c.call_tool("digest_session", {"session": "billing-ab"})).structured_content
    assert len(out["digest"]) <= 600
    assert out["digest"].endswith("says something useful.")


async def test_the_caller_can_choose_the_language_of_the_digest(setup):
    summarize = Summarizer()
    async with Client(bridge(setup, TURNS, summarize)) as c:
        await c.call_tool("digest_session", {"session": "billing-ab", "language": "norsk"})
        await c.call_tool("digest_session", {"session": "billing-ab"})
    assert summarize.languages == ["norsk", None]
