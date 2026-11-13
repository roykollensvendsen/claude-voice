"""Questions and approvals from any session, answered from the phone while the companion app is open."""

import asyncio
import http.server
import json
import logging
import threading

import httpx
import pytest
from fakes import FakeClaude

from claude_voice import api, phone_hook, prompts
from claude_voice.oauth import OAuthProvider
from claude_voice.server import build_app, build_server
from claude_voice.sessions import SessionManager
from claude_voice.store import Store

SECRET = "s3cret-token-for-the-prompt-tests-x"
BEARER = {"Authorization": f"Bearer {SECRET}"}
HOST = "http://127.0.0.1:8811"

QUESTION = {
    "session_id": "s1",
    "kind": "question",
    "question": {
        "questions": [
            {
                "question": "Which colour?",
                "header": "Colour",
                "multiSelect": False,
                "options": [{"label": "red", "description": "warm"}, {"label": "blue", "description": "cool"}],
            }
        ]
    },
}
APPROVAL = {
    "session_id": "s1",
    "kind": "approval",
    "tool": "Bash",
    "input": {"command": "rm -rf build", "description": "clean"},
}


@pytest.fixture
def app(tmp_path):
    (tmp_path / "home").mkdir()
    store = Store(tmp_path / "b.db")
    oauth = OAuthProvider(store, SECRET, HOST, static_token=SECRET)
    m = SessionManager(store, project_root=tmp_path / "home", client_factory=FakeClaude())
    return build_app(build_server(m, oauth=oauth, conversations=lambda **k: [], live_dir=tmp_path / "live"))


def client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=HOST)


async def test_a_prompt_waits_for_the_phone_only_while_the_app_is_open(app):
    async with client(app) as c:
        closed = (await c.post("/api/v1/prompts", json=QUESTION, headers=BEARER)).json()
        with api.HUB.subscribe():
            open_ = (await c.post("/api/v1/prompts", json=QUESTION, headers=BEARER)).json()
    assert closed["wait"] is False and open_["wait"] is True


async def test_an_answer_from_the_phone_reaches_the_waiting_session_and_is_logged(app, caplog):
    async with client(app) as c:
        with api.HUB.subscribe() as events:
            made = (await c.post("/api/v1/prompts", json=QUESTION, headers=BEARER)).json()
            listed = (await c.get("/api/v1/prompts", headers=BEARER)).json()["prompts"]
            waiting = asyncio.create_task(c.get(f"/api/v1/prompts/{made['id']}/wait?seconds=5", headers=BEARER))
            await asyncio.sleep(0.2)
            with caplog.at_level(logging.INFO, logger="claude_voice"):
                answered = await c.post(f"/api/v1/prompts/{made['id']}", json={"answer": "blue"}, headers=BEARER)
            got = (await asyncio.wait_for(waiting, 5)).json()
            again = await c.get("/api/v1/prompts", headers=BEARER)
            seen = []
            while not events.empty():
                seen.append(events.get_nowait()[0])
    assert listed[0]["kind"] == "question" and listed[0]["question"]["options"][1]["label"] == "blue"
    assert answered.status_code == 200 and got["answer"] == "blue"
    assert again.json()["prompts"] == []
    assert "prompt" in seen and "prompt_closed" in seen
    assert any(
        "answered" in r.getMessage() and "blue" in r.getMessage() and "s1" in r.getMessage() for r in caplog.records
    )


@pytest.mark.parametrize(
    ("body", "answer"),
    [
        (QUESTION, {"answer": "green"}),  # not one of the options
        (QUESTION, {"decision": "allow"}),  # an approval's answer on a question
        (QUESTION, {"answer": ["red", "blue"]}),  # two choices where only one is allowed
        (APPROVAL, {"answer": "red"}),  # a question's answer on an approval
    ],
)
async def test_an_answer_that_does_not_fit_the_prompt_is_refused(app, body, answer):
    async with client(app) as c:
        made = (await c.post("/api/v1/prompts", json=body, headers=BEARER)).json()
        refused = await c.post(f"/api/v1/prompts/{made['id']}", json=answer, headers=BEARER)
    assert refused.status_code == 409


async def test_an_approval_shows_what_would_run(app):
    async with client(app) as c:
        await c.post("/api/v1/prompts", json=APPROVAL, headers=BEARER)
        listed = (await c.get("/api/v1/prompts", headers=BEARER)).json()["prompts"]
    assert listed[0]["tool"] == "Bash" and listed[0]["input"] == "rm -rf build"


async def test_a_prompt_answered_at_the_screen_or_timed_out_is_gone_from_the_phone(app):
    async with client(app) as c:
        made = (await c.post("/api/v1/prompts", json=APPROVAL, headers=BEARER)).json()
        await c.post(f"/api/v1/prompts/{made['id']}/close", json={"how": "timeout"}, headers=BEARER)
        late = await c.post(f"/api/v1/prompts/{made['id']}", json={"decision": "allow"}, headers=BEARER)
    assert late.status_code == 404


# -- the hook, as Claude Code runs it ----------------------------------------------------


class Bridge:
    """Stands in for the bridge as the hook sees it."""

    def __init__(self, wait=True, answer=None):
        self.wait, self.answer = wait, answer
        self.posted, self.closed = [], []

    def open(self, body):
        self.posted.append(body)
        return {"id": "p1", "wait": self.wait}

    def wait_for(self, prompt_id, seconds):
        return self.answer

    def close(self, prompt_id, how):
        self.closed.append(how)


def question_input():
    return {
        "hook_event_name": "PreToolUse",
        "session_id": "s1",
        "tool_name": "AskUserQuestion",
        "tool_input": QUESTION["question"],
    }


def test_the_hook_steps_aside_at_once_when_the_app_is_not_open():
    bridge = Bridge(wait=False)
    assert phone_hook.handle(question_input(), bridge, timeout=5) == {}


def test_the_hook_closes_a_prompt_it_steps_aside_from():
    bridge = Bridge(wait=False)
    phone_hook.handle(question_input(), bridge, timeout=5)
    assert bridge.closed == ["screen"]


def test_a_hook_that_fails_while_waiting_still_closes_its_prompt():
    class Failing(Bridge):
        def wait_for(self, prompt_id, seconds):
            raise OSError("connection dropped")

    bridge = Failing()
    assert phone_hook.handle(question_input(), bridge, timeout=5) == {}
    assert bridge.closed == ["screen"]


def test_the_hook_hands_the_phones_choice_back_as_the_questions_answer():
    out = phone_hook.handle(question_input(), Bridge(answer={"answer": "blue"}), timeout=5)
    spec = out["hookSpecificOutput"]
    assert spec["permissionDecision"] == "allow" and spec["updatedInput"]["answers"] == {"Which colour?": "blue"}


def test_the_hook_hands_the_phones_decision_back_to_a_permission_prompt():
    hook_input = {
        "hook_event_name": "PermissionRequest",
        "session_id": "s1",
        "tool_name": "Bash",
        "tool_input": {"command": "ls"},
    }
    allow = phone_hook.handle(hook_input, Bridge(answer={"decision": "allow"}), timeout=5)
    deny = phone_hook.handle(hook_input, Bridge(answer={"decision": "deny", "reason": "not now"}), timeout=5)
    assert allow["hookSpecificOutput"]["decision"] == {"behavior": "allow"}
    assert deny["hookSpecificOutput"]["decision"]["behavior"] == "deny"


def test_with_no_answer_in_time_the_hook_gives_the_question_back_to_the_screen():
    bridge = Bridge(answer=None)
    assert phone_hook.handle(question_input(), bridge, timeout=0) == {}
    assert bridge.closed == ["timeout"]


def test_a_broken_bridge_never_stops_a_session():
    class Broken(Bridge):
        def open(self, body):
            raise OSError("bridge down")

    assert phone_hook.handle(question_input(), Broken(), timeout=5) == {}


def test_the_hook_leaves_every_other_tool_and_event_alone():
    bridge = Bridge(answer={"answer": "blue"})
    for hook_input in (
        {"hook_event_name": "PreToolUse", "session_id": "s1", "tool_name": "Bash", "tool_input": {}},
        {"hook_event_name": "PostToolUse", "session_id": "s1", "tool_name": "AskUserQuestion", "tool_input": {}},
    ):
        assert phone_hook.handle(hook_input, bridge, timeout=5) == {}
    assert bridge.posted == []


def test_the_hook_joins_several_choices_picked_on_the_phone():
    out = phone_hook.handle(question_input(), Bridge(answer={"answer": {"text": ["red", "blue"]}}), timeout=5)
    assert out["hookSpecificOutput"]["updatedInput"]["answers"] == {"Which colour?": "red, blue"}


def test_a_refusal_from_the_phone_tells_claude_why():
    hook_input = {"hook_event_name": "PermissionRequest", "session_id": "s1", "tool_name": "Bash", "tool_input": {}}
    given = phone_hook.handle(hook_input, Bridge(answer={"decision": "deny", "reason": "not now"}), timeout=5)
    bare = phone_hook.handle(hook_input, Bridge(answer={"decision": "maybe"}), timeout=5)
    assert given["hookSpecificOutput"]["decision"] == {"behavior": "deny", "message": "not now"}
    assert bare["hookSpecificOutput"]["decision"] == {"behavior": "deny", "message": "Refused from the phone."}


def test_a_question_never_opens_a_second_prompt_as_an_approval():
    # Claude Code also asks permission for the question itself once the question hook steps aside,
    # measured: PermissionRequest fires for AskUserQuestion 0.1 s after PreToolUse.
    bridge = Bridge(answer={"decision": "allow"})
    hook_input = {**question_input(), "hook_event_name": "PermissionRequest"}
    assert phone_hook.handle(hook_input, bridge, timeout=5) == {}
    assert bridge.posted == []


def test_a_call_with_several_questions_goes_to_the_screen():
    # Claude Code drops a question left out of the answers without a word, measured; the phone shows only one.
    two = question_input()
    second = {**two["tool_input"]["questions"][0], "question": "Which size?"}
    two["tool_input"] = {"questions": [two["tool_input"]["questions"][0], second]}
    bridge = Bridge(answer={"answer": "blue"})
    assert phone_hook.handle(two, bridge, timeout=5) == {}
    assert bridge.posted == []


def test_the_hook_leaves_alone_the_session_the_voice_is_talking_to():
    # Claude Code holds every hook's answer until the slowest is done, measured, so a waiting
    # phone hook would delay the voice's own refusal of the box for minutes.
    bridge = Bridge(answer={"answer": "blue"})
    assert phone_hook.handle(question_input(), bridge, timeout=5, spoken_to="s1") == {}
    assert bridge.posted == []
    assert phone_hook.handle(question_input(), Bridge(answer={"answer": "blue"}), timeout=5, spoken_to="s2")


def test_the_session_the_voice_talks_to_is_read_from_its_target_file(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    assert phone_hook.spoken_to() == ""
    (tmp_path / "voice-bridge").mkdir()
    target = tmp_path / "voice-bridge" / "target.json"
    target.write_text("not json")
    assert phone_hook.spoken_to() == ""
    target.write_text(json.dumps({"kind": "project", "claude_session_id": "s1"}))
    assert phone_hook.spoken_to() == ""
    target.write_text(json.dumps({"kind": "session", "claude_session_id": "s1"}))
    assert phone_hook.spoken_to() == "s1"


class LostPrompts(http.server.BaseHTTPRequestHandler):
    """A bridge that has restarted: it knows no prompt any more."""

    waits = 0

    def do_GET(self):  # noqa: N802 - the name http.server calls
        LostPrompts.waits += 1
        self.send_response(404)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"error": "this prompt is gone"}')

    def log_message(self, *args):
        pass


def test_a_prompt_the_bridge_has_lost_goes_back_to_the_screen_at_once():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), LostPrompts)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        LostPrompts.waits = 0
        bridge = phone_hook.HttpBridge(f"http://127.0.0.1:{server.server_address[1]}", "t")
        with pytest.raises(LookupError):
            bridge.wait_for("p1", 300)
        assert LostPrompts.waits == 1
    finally:
        server.shutdown()


def test_the_hook_is_given_to_claude_code_only_for_questions_and_permission_prompts():
    entries = phone_hook.settings_entries("/usr/bin/python3")
    assert set(entries) == {"PreToolUse", "PermissionRequest"}
    assert entries["PreToolUse"][0]["matcher"] == "AskUserQuestion"
    assert json.dumps(entries).count("claude_voice.phone_hook") == 2


def test_installing_the_hook_keeps_every_other_setting_and_hook_and_can_run_twice(tmp_path):
    settings = tmp_path / "settings.json"
    other = {"type": "command", "command": "$HOME/.claude/hooks/no_choice_box_while_spoken_to.py"}
    settings.write_text(
        json.dumps({"model": "opus", "hooks": {"PreToolUse": [{"matcher": "AskUserQuestion", "hooks": [other]}]}})
    )
    phone_hook.install(settings, "/opt/cv/bin/python")
    phone_hook.install(settings, "/opt/cv/bin/python")
    after = json.loads(settings.read_text())
    assert after["model"] == "opus"
    pre = after["hooks"]["PreToolUse"]
    commands = [h["command"] for m in pre for h in m["hooks"]]
    assert other["command"] in commands and commands.count("/opt/cv/bin/python -m claude_voice.phone_hook") == 1
    assert len(after["hooks"]["PermissionRequest"]) == 1
    assert (tmp_path / "settings.json.bak").exists()  # the file as it was, kept beside it


# -- prompts nobody waits on any more ----------------------------------------------------


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


ASKED_MS = 1794564000000  # 2026-11-13T10:00:00Z, when the prompt below was asked


def book_with():
    clock, seen = Clock(), []
    book = prompts.Book(lambda kind, data: seen.append((kind, data)), clock)
    asked = "2026-11-13T10:00:00+00:00"
    book.add({"id": "p1", "session": "s1", "kind": "question", "asked_at": asked, "answer": None})
    return book, clock, seen


def test_a_prompt_the_hook_still_waits_on_is_never_closed():
    book, clock, _ = book_with()
    with book.waiting(book.items["p1"]):
        clock.now += 3600
        assert book.sweep([]) == []
    clock.now += prompts.GRACE - 1  # between two of the hook's wait calls
    assert book.sweep([]) == []
    assert "p1" in book.items


@pytest.mark.parametrize(
    ("live", "how"),
    [
        ([], "ended"),
        ([{"sessionId": "s1", "status": "busy", "statusUpdatedAt": ASKED_MS + 5000}], "moved_on"),
        ([{"sessionId": "s1", "status": "waiting", "statusUpdatedAt": ASKED_MS + 5000}], "hook_gone"),
        ([{"sessionId": "s1", "status": "busy", "statusUpdatedAt": ASKED_MS - 5000}], "hook_gone"),
    ],
)
def test_a_prompt_with_no_hook_waiting_on_it_closes(live, how):
    book, clock, seen = book_with()
    clock.now += prompts.GRACE + 1
    assert book.sweep(live) == [("p1", how)]
    assert book.items == {} and seen == [("prompt_closed", {"id": "p1", "how": how})]


def test_an_answered_prompt_the_hook_never_collected_goes_quietly():
    book, clock, seen = book_with()
    book.items["p1"]["answer"] = {"answer": "blue"}
    clock.now += prompts.GRACE + 1
    book.sweep([])
    assert book.items == {} and seen == []  # it was announced as closed when it was answered


async def test_the_phone_no_longer_lists_a_prompt_whose_hook_went_away(tmp_path):
    (tmp_path / "home").mkdir()
    store = Store(tmp_path / "b.db")
    oauth = OAuthProvider(store, SECRET, HOST, static_token=SECRET)
    m = SessionManager(store, project_root=tmp_path / "home", client_factory=FakeClaude())
    server = build_server(m, oauth=oauth, conversations=lambda **k: [], live_dir=tmp_path / "live")
    clock = server.prompt_book.clock = Clock()
    async with client(build_app(server)) as c:
        made = (await c.post("/api/v1/prompts", json=APPROVAL, headers=BEARER)).json()
        await c.get(f"/api/v1/prompts/{made['id']}/wait?seconds=0", headers=BEARER)
        clock.now += prompts.GRACE + 1
        server.sweep_prompts()
        listed = (await c.get("/api/v1/prompts", headers=BEARER)).json()["prompts"]
    assert listed == []
