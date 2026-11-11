"""Questions and approvals from any session, answered from the phone while the companion app is open."""

import asyncio
import json
import logging

import httpx
import pytest
from fakes import FakeClaude

from claude_voice import api, phone_hook
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
    assert bridge.closed == []


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
