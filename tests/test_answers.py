"""A session's choice box, read out and answered by voice while the phone hook waits on it."""

import asyncio
import json
import os

import httpx
import pytest
from claude_agent_sdk import project_key_for_directory
from fakes import FakeClaude
from mcp.client import Client

from claude_voice import answers, prompts
from claude_voice.oauth import OAuthProvider
from claude_voice.server import build_app, build_server
from claude_voice.sessions import SessionManager
from claude_voice.store import Store

SECRET = "s3cret-token-for-the-answer-tests-x"
BEARER = {"Authorization": f"Bearer {SECRET}"}
HOST = "http://127.0.0.1:8811"


def question(multi=False):
    return {
        "session_id": "c-1",
        "kind": "question",
        "question": {
            "questions": [
                {
                    "question": "Which colour?",
                    "header": "Colour",
                    "multiSelect": multi,
                    "options": [{"label": "Red", "description": "warm"}, {"label": "Blue", "description": "cool"}],
                }
            ]
        },
    }


@pytest.fixture
def server(tmp_path):
    (tmp_path / "home").mkdir()
    live = tmp_path / "live"
    live.mkdir()
    row = {
        "pid": os.getpid(),
        "sessionId": "c-1",
        "name": "painter",
        "status": "waiting",
        "cwd": str(tmp_path / "home"),
    }
    (live / "1.json").write_text(json.dumps({**row, "waitingFor": "input needed"}))
    store = Store(tmp_path / "b.db")
    oauth = OAuthProvider(store, SECRET, HOST, static_token=SECRET)
    m = SessionManager(store, project_root=tmp_path / "home", client_factory=FakeClaude())

    async def deliver(name, text):
        return {"delivered": True, "detail": "queued"}

    return build_server(
        m,
        oauth=oauth,
        conversations=lambda **k: [],
        live_dir=live,
        deliver=deliver,
        projects_dir=tmp_path / "projects",
    )


def http(server):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=build_app(server)), base_url=HOST)


async def call(c, name, **args):
    return (await c.call_tool(name, args)).structured_content


async def opened_and_waiting(h, body):
    """Open a prompt as the hook does, and start the hook's wait on it."""
    made = (await h.post("/api/v1/prompts", json=body, headers=BEARER)).json()
    wait = asyncio.create_task(h.get(f"/api/v1/prompts/{made['id']}/wait?seconds=5", headers=BEARER))
    await asyncio.sleep(0.1)
    return made["id"], wait


async def test_the_voice_hears_a_waiting_question_with_numbered_options(server):
    async with http(server) as h, Client(server) as c:
        pid, wait = await opened_and_waiting(h, question())
        listed = await call(c, "list_pending_approvals")
        await call(c, "answer_question", question_id=pid, choice="1")
        await wait
    assert listed["approvals"] == []
    (asked,) = listed["questions"]
    assert asked["id"] == pid and asked["session_name"] == "painter" and asked["text"] == "Which colour?"
    assert asked["options"] == [
        {"number": 1, "label": "Red", "description": "warm"},
        {"number": 2, "label": "Blue", "description": "cool"},
    ]


async def test_an_option_is_chosen_by_its_number_or_its_label(server):
    async with http(server) as h, Client(server) as c:
        pid, wait = await opened_and_waiting(h, question())
        said = await call(c, "answer_question", question_id=pid, choice="2")
        first = (await wait).json()
        pid, wait = await opened_and_waiting(h, question())
        await call(c, "answer_question", question_id=pid, choice="red")
        second = (await wait).json()
    assert said["answered"] is True
    assert first == {"answer": "Blue"} and second == {"answer": "Red"}


async def test_several_options_and_the_owners_own_words_reach_the_session(server):
    async with http(server) as h, Client(server) as c:
        pid, wait = await opened_and_waiting(h, question(multi=True))
        await call(c, "answer_question", question_id=pid, choice=["1", "Blue"])
        several = (await wait).json()
        pid, wait = await opened_and_waiting(h, question())
        await call(c, "answer_question", question_id=pid, own_words="green, please")
        own = (await wait).json()
    assert several == {"answer": ["Red", "Blue"]} and own == {"answer": {"text": "green, please"}}


async def test_a_spoken_choice_that_is_not_an_option_is_refused_with_the_options(server):
    async with http(server) as h, Client(server) as c:
        pid, wait = await opened_and_waiting(h, question())
        refused = await call(c, "answer_question", question_id=pid, choice="purple")
        still = await call(c, "list_pending_approvals")
        await call(c, "answer_question", question_id=pid, choice="1")
        await wait
    assert refused["answered"] is False and "purple" in refused["error"]
    assert [o["label"] for o in refused["options"]] == ["Red", "Blue"]
    assert [q["id"] for q in still["questions"]] == [pid]


async def test_a_question_no_hook_waits_on_is_refused_as_on_the_screen(server):
    async with http(server) as h, Client(server) as c:
        gone = await call(c, "answer_question", question_id="0123456789abcdef", choice="1")
        made = (await h.post("/api/v1/prompts", json=question(), headers=BEARER)).json()
        unwaited = await call(c, "answer_question", question_id=made["id"], choice="1")
        pid, wait = await opened_and_waiting(h, question())
        first = await call(c, "answer_question", question_id=pid, choice="1")
        second = await call(c, "answer_question", question_id=pid, choice="2")  # before the hook collected
        await wait
    assert gone["answered"] is False and gone["status"] == 410 and "screen" in gone["say"]
    assert unwaited["status"] == 410 and "screen" in unwaited["say"]
    assert first["answered"] is True and second["status"] == 410
    book, prompt, _ = asked()  # answered, and the hook has not yet collected the answer
    with book.waiting(prompt):
        answers.answer(book, "q1", "1", None)
        again = answers.answer(book, "q1", "2", None)
    assert again[0] == 410 and prompt["answer"] == {"answer": "Red"}


async def test_an_answered_question_is_not_listed_again(server):
    async with http(server) as h, Client(server) as c:
        pid, wait = await opened_and_waiting(h, question())
        await call(c, "answer_question", question_id=pid, choice="1")
        await wait
        again = await call(c, "answer_question", question_id=pid, choice="1")
        listed = await call(c, "list_pending_approvals")
    assert listed["questions"] == [] and again["status"] == 410


async def test_a_question_is_news_once_a_hook_waits_on_it(server):
    async with http(server) as h, Client(server) as c:
        cursor = (await call(c, "whats_new"))["cursor"]
        made = (await h.post("/api/v1/prompts", json=question(), headers=BEARER)).json()
        before = (await call(c, "whats_new", cursor=cursor))["events"]
        wait = asyncio.create_task(h.get(f"/api/v1/prompts/{made['id']}/wait?seconds=5", headers=BEARER))
        await asyncio.sleep(0.1)
        after = (await call(c, "whats_new", cursor=cursor))["events"]
        await h.get(f"/api/v1/prompts/{made['id']}/wait?seconds=0", headers=BEARER)  # a second wait is no news
        again = (await call(c, "whats_new", cursor=cursor))["events"]
        await call(c, "answer_question", question_id=made["id"], choice="1")
        await wait
    assert [e for e in before if e["kind"] == "needs_answer"] == []
    (news,) = [e for e in after if e["kind"] == "needs_answer"]
    assert news["session"] == "painter" and "Which colour?" in news["text"]
    assert len([e for e in again if e["kind"] == "needs_answer"]) == 1


def test_a_choice_needs_either_an_option_or_the_owners_own_words():
    asked = {"options": [{"label": "Red"}], "multi": False}
    assert "either" in answers.body_for(asked, None, None)["error"]
    assert "either" in answers.body_for(asked, "1", "red")["error"]


def test_an_approval_is_not_answered_as_a_question():
    book = prompts.Book()
    book.add({"id": "p1", "session": "s", "kind": "approval", "answer": None, "done": asyncio.Event()})
    status, said = answers.answer(book, "p1", "1", None)
    assert status == 409 and said["answered"] is False


def asked(multi=False):
    """A question as the book holds it, with a clock the test turns."""
    now = [100.0]
    book = prompts.Book(clock=lambda: now[0])
    options = [{"label": "Red", "description": ""}, {"label": "Blue", "description": ""}]
    prompt = {
        "id": "q1",
        "session": "s",
        "kind": "question",
        "answer": None,
        "done": asyncio.Event(),
        "asked_at": "2026-11-13T12:00:00+00:00",
        "question": {"text": "Which colour?", "header": "Colour", "options": options, "multi": multi},
    }
    book.add(prompt)
    return book, prompt, now


def test_a_prompt_counts_as_waited_on_only_while_a_hook_waits_or_did_a_moment_ago():
    book, prompt, now = asked()
    never = book.hook_waits(prompt), answers.waiting(book, [])
    with book.waiting(prompt):
        during = book.hook_waits(prompt), len(answers.waiting(book, []))
    now[0] += prompts.RECENT - 1
    between = book.hook_waits(prompt)  # the hook's next wait is on its way
    now[0] += 2
    after = book.hook_waits(prompt), answers.waiting(book, [])
    status, said = answers.answer(book, "q1", "1", None)
    assert never == (False, []) and during == (True, 1) and between is True and after == (False, [])
    assert status == 410 and said["answered"] is False and "screen" in said["say"]
    assert prompt["answer"] is None


def test_an_empty_pick_is_refused():
    for multi in (False, True):
        book, prompt, _ = asked(multi)
        with book.waiting(prompt):
            status, said = answers.answer(book, "q1", [], None)
        assert status == 409 and said["answered"] is False
        assert "number" in said["error"] and [o["label"] for o in said["options"]] == ["Red", "Blue"]
        assert prompt["answer"] is None


def test_a_choice_named_twice_counts_once():
    book, prompt, _ = asked(multi=True)
    with book.waiting(prompt):
        status, said = answers.answer(book, "q1", ["1", "red", "Blue", "2"], None)
    assert status == 200 and said["answer"] == ["Red", "Blue"]


async def test_a_message_to_a_session_waiting_on_a_box_says_what_it_waits_on(server):
    async with http(server) as h, Client(server) as c:
        pid, wait = await opened_and_waiting(h, question())
        told = await call(c, "message_active_session", session="painter", message="also blue")
        adopted = await call(c, "attach_conversation", claude_session_id="c-1")
        sent = await call(c, "send_task", session_id=adopted["id"], prompt="go on")
        await call(c, "answer_question", question_id=pid, choice="1")
        await wait
    options = [
        {"number": 1, "label": "Red", "description": "warm"},
        {"number": 2, "label": "Blue", "description": "cool"},
    ]
    for out in (told, sent):
        assert out["waiting_on"] == {
            "kind": "question",
            "question": "Which colour?",
            "options": options,
            "prompt_id": pid,
        }
        assert out["say"] == answers.QUEUED
    assert told["delivered"] is True


def test_a_box_no_hook_holds_is_read_from_the_transcript_and_a_busy_session_waits_on_nothing(tmp_path):
    book = prompts.Book()
    row = {"sessionId": "q", "cwd": "/w", "status": "waiting", "waitingFor": "input needed"}
    tool = {
        "type": "tool_use",
        "id": "t1",
        "name": "AskUserQuestion",
        "input": {"questions": [{"question": "Ship it?", "options": [{"label": "Yes"}, {"label": "No"}]}]},
    }
    path = tmp_path / project_key_for_directory("/w") / "q.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"type": "assistant", "message": {"content": [tool]}}) + "\n")
    scanned = answers.waiting_on(row, book, tmp_path)
    approval = answers.waiting_on({**row, "waitingFor": "permission prompt"}, book, tmp_path)
    busy = answers.waiting_on({**row, "status": "busy"}, book, tmp_path)
    assert scanned == {
        "kind": "question",
        "question": "Ship it?",
        "options": [{"number": 1, "label": "Yes"}, {"number": 2, "label": "No"}],
        "prompt_id": None,
    }
    assert approval == {"kind": "approval", "question": None, "options": [], "prompt_id": None}
    assert busy is None


def test_a_wait_goes_on_and_is_counted_off_when_its_news_fails():
    def broken(prompt):
        raise RuntimeError("the news database is locked")

    book = prompts.Book(waited_on=broken)
    prompt = {"id": "q1", "session": "s", "kind": "question", "answer": None}
    book.add(prompt)
    with book.waiting(prompt):
        inside = prompt["hook"]["waiting"]
    assert inside == 1 and prompt["hook"]["waiting"] == 0
