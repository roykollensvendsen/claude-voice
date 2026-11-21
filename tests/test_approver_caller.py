"""Who may call the approver's tools: only the approver session, proven by what its own hook reported."""

from __future__ import annotations

import asyncio
import dataclasses
import re
import sys

import httpx
import pytest
from fakes import FakeClaude

from claude_voice import answers, approver, config, phone_hook, prompts
from claude_voice.oauth import OAuthProvider
from claude_voice.server import _duty, build_app, build_server
from claude_voice.sessions import SessionManager
from claude_voice.store import Store

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX paths and file modes")

APPROVER = "approver-session-1"
WORKER = "role-session-1"
TOOL = "mcp__claude_ai_claude-voice__approver_answer"
LIST = "mcp__claude_ai_claude-voice__approver_boxes"
SECRET = "s3cret-token-for-the-caller-tests-xx"
BEARER = {"Authorization": f"Bearer {SECRET}"}
HOST = "http://127.0.0.1:8811"


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "repo" / "src").mkdir(parents=True)
    return tmp_path / "repo"


@pytest.fixture
def duty(tmp_path, clock):
    return approver.Duty(
        on=True,
        role=lambda s: s in (WORKER, APPROVER),
        approver=lambda s: s == APPROVER,
        clock=clock,
        log=tmp_path / "approver" / "log.jsonl",
    )


@pytest.fixture
def book(clock):
    return prompts.Book(clock=clock)


def box(book, repo, session=WORKER):
    prompt = {
        "id": f"p{len(book.items)}",
        "session": session,
        "kind": "approval",
        "asked_at": "2026-11-21T12:00:00+00:00",
        "answer": None,
        "done": asyncio.Event(),
        "tool": "Bash",
        "input": "uv run pytest",
        "cut": False,
        "raw_input": {"command": "uv run pytest"},
        "cwd": str(repo),
        "routed": {"to": "owner", "line": None, "lines": [], "shape": False},
    }
    book.add(prompt)
    prompt["hook"]["waiting"] = 1
    return prompt


def settle(prompt, body):
    answers.settle(prompt, body, "approver", lambda *event: None)


def call(prompt_id, decision="allow", reason="the session's own tests"):
    """The arguments of one approver_answer call, as both the hook and the tool see them."""
    return {"id": prompt_id, "decision": decision, "reason": reason}


def answer(book, duty, args):
    return approver.answer(
        book, duty, args["id"], args["decision"], args["reason"], settle, phone_hook.args_digest(args)
    )


def test_an_approver_call_without_its_own_hooks_report_is_refused(book, repo, duty):
    prompt = box(book, repo)
    assert (
        approver.answer(book, duty, prompt["id"], "allow", "x", settle, phone_hook.args_digest(call(prompt["id"])))[
            "refused"
        ]
        == approver.NOT_PROVEN
    )
    assert "refused" in approver.boxes(book, duty, {}, phone_hook.args_digest({}))
    assert prompt["answer"] is None


def test_a_report_from_another_session_is_refused(book, repo, duty):
    prompt = box(book, repo)
    args = call(prompt["id"])
    duty.report(WORKER, TOOL, phone_hook.args_digest(args))  # the box's own session, not the approver
    assert answer(book, duty, args)["refused"] == approver.NOT_PROVEN
    assert prompt["answer"] is None


def test_a_report_for_other_arguments_is_refused(book, repo, duty):
    prompt = box(book, repo)
    duty.report(APPROVER, TOOL, phone_hook.args_digest(call(prompt["id"], decision="deny")))
    assert answer(book, duty, call(prompt["id"]))["refused"] == approver.NOT_PROVEN
    duty.report(APPROVER, LIST, phone_hook.args_digest(call(prompt["id"])))  # the other tool's report
    assert answer(book, duty, call(prompt["id"]))["refused"] == approver.NOT_PROVEN
    assert prompt["answer"] is None


def test_a_report_older_than_five_seconds_is_refused(book, repo, duty, clock):
    prompt = box(book, repo)
    args = call(prompt["id"])
    duty.report(APPROVER, TOOL, phone_hook.args_digest(args))
    clock.now += approver.PROOF_SECONDS + 0.1
    assert answer(book, duty, args)["refused"] == approver.NOT_PROVEN


def test_each_report_is_used_once(book, repo, duty):
    duty.report(APPROVER, LIST, phone_hook.args_digest({}))
    assert isinstance(approver.boxes(book, duty, {}, phone_hook.args_digest({})), list)
    assert approver.boxes(book, duty, {}, phone_hook.args_digest({}))["refused"] == approver.NOT_PROVEN


def test_a_proven_call_from_the_approver_answers_the_box(book, repo, duty):
    prompt = box(book, repo)
    args = call(prompt["id"])
    duty.report(APPROVER, TOOL, phone_hook.args_digest(args))
    assert answer(book, duty, args)["decided"] == "allow"
    assert prompt["answer"]["decision"] == "allow"


def test_the_approver_never_answers_its_own_box(book, repo, duty):
    own = box(book, repo, session=APPROVER)
    args = call(own["id"])
    duty.report(APPROVER, TOOL, phone_hook.args_digest(args))
    assert "its own box" in answer(book, duty, args)["refused"]
    assert own["answer"] is None


class HookBridge:
    """The bridge as the hook sees it, keeping what the hook reported."""

    def __init__(self):
        self.reports, self.posted = [], []

    def report(self, body):
        self.reports.append(body)

    def open(self, body):
        self.posted.append(body)
        return {"id": "p1", "wait": False}

    def wait_for(self, prompt_id, seconds):
        return None

    def close(self, prompt_id, how):
        pass


def test_the_hook_reports_each_approver_call_with_a_hash_of_its_arguments():
    bridge = HookBridge()
    args = call("p0")
    hook_input = {"hook_event_name": "PreToolUse", "session_id": APPROVER, "tool_name": TOOL, "tool_input": args}
    assert phone_hook.handle(hook_input, bridge) == {}  # it reports and decides nothing
    assert bridge.reports == [{"session_id": APPROVER, "tool": TOOL, "args_sha256": phone_hook.args_digest(args)}]
    other = {**hook_input, "tool_name": "mcp__claude_ai_claude-voice__answer_question"}
    assert phone_hook.handle(other, bridge) == {}
    assert len(bridge.reports) == 1 and bridge.posted == []
    assert phone_hook.args_digest({"b": 1, "a": "x"}) == phone_hook.args_digest({"a": "x", "b": 1})


def test_the_hook_runs_before_every_approver_tool():
    matchers = [entry["matcher"] for entry in phone_hook.settings_entries()["PreToolUse"]]
    approvers = [m for m in matchers if re.fullmatch(m, TOOL)]
    assert approvers and all(re.fullmatch(m, LIST) for m in approvers)
    assert not any(re.fullmatch(m, "mcp__claude_ai_claude-voice__answer_question") for m in approvers)


def bridge_with(tmp_path, duty):
    (tmp_path / "home").mkdir(exist_ok=True)
    store = Store(tmp_path / "b.db")
    oauth = OAuthProvider(store, SECRET, HOST, static_token=SECRET)
    m = SessionManager(store, project_root=tmp_path / "home", client_factory=FakeClaude())
    server = build_server(m, oauth=oauth, conversations=lambda **k: [], live_dir=tmp_path / "live", duty=duty)
    return build_app(server)


async def test_the_bridge_takes_a_report_only_with_its_token(tmp_path, repo, duty, book):
    app = bridge_with(tmp_path, duty)
    args = call("p0")
    body = {"session_id": APPROVER, "tool": TOOL, "args_sha256": phone_hook.args_digest(args)}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=HOST) as c:
        refused = await c.post("/api/v1/approver/reports", json=body)
        taken = await c.post("/api/v1/approver/reports", json=body, headers=BEARER)
        bad = await c.post("/api/v1/approver/reports", json={**body, "args_sha256": 7}, headers=BEARER)
    assert refused.status_code == 401 and taken.status_code == 204 and bad.status_code == 400
    box(book, repo)
    assert answer(book, duty, args)["decided"] == "allow"  # the one report the bridge took


def test_the_approver_can_be_switched_on_once_its_caller_is_checked(tmp_path):
    file = tmp_path / "config.toml"
    file.write_text("[approver]\non = true\n", encoding="utf-8")
    assert config.load(file, own_clone=None, env={"HOME": str(tmp_path)}).approver_on is True


def test_only_the_session_launched_as_the_approver_role_is_the_approver(tmp_path):
    (tmp_path / "home").mkdir()
    store = Store(tmp_path / "b.db")
    manager = SessionManager(store, project_root=tmp_path / "home", client_factory=FakeClaude())
    (tmp_path / "config.toml").write_text("", encoding="utf-8")
    repos = config.load(tmp_path / "config.toml", own_clone=None, env={"HOME": str(tmp_path)})
    duty = _duty(None, dataclasses.replace(repos, approver_on=True), manager)
    assert not duty.approver(APPROVER)  # no approver launched yet: nobody is it
    store.set_setting("role:builder", {"name": "builder", "id": WORKER})
    store.set_setting("role:approver", {"name": "approver", "id": APPROVER})
    assert duty.approver(APPROVER) and not duty.approver(WORKER)
