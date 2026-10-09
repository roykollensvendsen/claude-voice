"""side_question: a quick answer about a busy session, read from what it wrote, never sent into it."""

import json
import os

import pytest
from claude_agent_sdk import ResultMessage
from fakes import FakeClaude
from mcp.client import Client
from test_live import Recorder, msg

from claude_voice.server import build_server
from claude_voice.sessions import SessionManager
from claude_voice.store import Store
from claude_voice.transcripts import SideReader

PID = os.getpid()
TURNS = [
    msg("user", "Run the tests"),
    msg("assistant", [{"type": "text", "text": "11 passed, 1 failed: test_export_totals. Fixing it now."}]),
]


@pytest.fixture
def setup(tmp_path):
    (tmp_path / "src" / "billing").mkdir(parents=True)
    live = tmp_path / "live"
    live.mkdir()
    (live / "x.json").write_text(
        json.dumps(
            {
                "pid": PID,
                "sessionId": "sid-1",
                "cwd": str(tmp_path / "src" / "billing"),
                "name": "billing-ab",
                "status": "busy",
            }
        )
    )
    return tmp_path


class Answerer:
    def __init__(self, answer="Elleve av tolv tester passerte. Den siste fikses nå."):
        self.answer = answer
        self.calls = []

    async def __call__(self, text, question, language=None):
        self.calls.append((text, question, language))
        return self.answer


def bridge(tmp_path, turns, side, deliver, side_chars=40_000):
    m = SessionManager(Store(tmp_path / "b.db"), project_root=tmp_path / "src", client_factory=FakeClaude())
    return build_server(
        m,
        conversations=lambda **k: [],
        live_dir=tmp_path / "live",
        deliver=deliver,
        read_transcript=lambda sid, directory: turns,
        side_answer=side,
        side_chars=side_chars,
    )


async def test_a_side_question_is_answered_from_the_transcript_and_never_sent_into_the_session(setup):
    side, deliver = Answerer(), Recorder()
    async with Client(bridge(setup, TURNS, side, deliver)) as c:
        out = (
            await c.call_tool(
                "side_question", {"session": "billing-ab", "question": "Gikk testene bra?", "language": "norsk"}
            )
        ).structured_content
    assert out == {"answer": side.answer, "considered_turns": 2, "cut": False}
    assert deliver.sent == []
    text, question, language = side.calls[0]
    assert "test_export_totals" in text and question == "Gikk testene bra?" and language == "norsk"


async def test_a_side_answer_is_short_enough_to_say_and_ends_on_a_whole_sentence(setup):
    long = " ".join(f"Setning nummer {i} sier noe nyttig." for i in range(30))
    async with Client(bridge(setup, TURNS, Answerer(long), Recorder())) as c:
        out = (await c.call_tool("side_question", {"session": "billing-ab", "question": "?"})).structured_content
    assert len(out["answer"]) <= 300 and out["answer"].endswith("sier noe nyttig.")


async def test_only_the_recent_end_is_read_and_the_answer_says_so(setup):
    turns = [msg("user", f"old {i} " + "x" * 200) for i in range(40)] + TURNS
    side = Answerer()
    async with Client(bridge(setup, turns, side, Recorder(), side_chars=600)) as c:
        out = (await c.call_tool("side_question", {"session": "billing-ab", "question": "?"})).structured_content
    assert out["cut"] is True and out["considered_turns"] < len(turns)
    assert "test_export_totals" in side.calls[0][0] and "old 0 " not in side.calls[0][0]


def done(text):
    return ResultMessage(
        subtype="success", duration_ms=1, duration_api_ms=1, is_error=False, num_turns=1, session_id="r", result=text
    )


class ReaderClient:
    def __init__(self, options):
        self.options = options
        self.prompts = []
        self.closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        self.closed = True

    async def query(self, prompt):
        self.prompts.append(prompt)

    async def receive_response(self):
        asked = [p for p in self.prompts if p != "/clear"]
        yield done("" if self.prompts[-1] == "/clear" else f"Svar {len(asked)}.")


async def test_the_reader_stays_warm_between_questions_and_starts_afresh_now_and_then():
    made = []

    def factory(options):
        made.append(ReaderClient(options))
        return made[-1]

    reader = SideReader(client_factory=factory, fresh_after=2)
    answers = [await reader("Owner: hi", "Hva skjer?", "norsk") for _ in range(3)]
    assert answers == ["Svar 1.", "Svar 2.", "Svar 1."]
    assert len(made) == 2 and made[0].closed
    assert made[0].options.model == "haiku" and made[0].options.tools == []
    assert "claude-voice-msg-" in str(made[0].options.cwd)
    assert "norsk" in made[0].prompts[0] and "Hva skjer?" in made[0].prompts[0]
    assert made[0].prompts[1] == "/clear"  # each answer is forgotten, so the next stays fast
    await reader.close()
