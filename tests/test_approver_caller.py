"""Who may call the approver's tools: only the approver session, proven by what its own hook reported."""

from __future__ import annotations

import asyncio
import dataclasses
import json
import os
import re
import socket
import subprocess
import sys

import httpx
import pytest
from fakes import FakeClaude

from claude_voice import answers, approver, config, lineage, phone_hook, prompts
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


def test_the_approvers_own_report_is_taken_before_another_sessions(book, duty):
    """Another session's matching report neither blocks the approver's call nor is used up by it."""
    digest = phone_hook.args_digest({})
    duty.report(WORKER, LIST, digest)
    duty.report(APPROVER, LIST, digest)
    assert isinstance(approver.boxes(book, duty, {}, digest), list)
    assert [r[1] for r in duty.reports] == [WORKER]


def test_the_bridge_keeps_only_the_newest_reports(duty):
    for n in range(approver.MAX_REPORTS + 5):
        duty.report(APPROVER, TOOL, str(n))
    assert len(duty.reports) == approver.MAX_REPORTS
    assert duty.reports[-1][3] == str(approver.MAX_REPORTS + 4)  # the oldest went first


def test_a_refused_call_is_logged_with_the_age_of_its_reports(book, repo, duty, clock, caplog):
    prompt = box(book, repo)
    args = call(prompt["id"])
    duty.report(APPROVER, TOOL, phone_hook.args_digest(args))
    clock.now += 6
    with caplog.at_level("WARNING", logger="claude_voice.approver"):
        assert answer(book, duty, args)["refused"] == approver.NOT_PROVEN
    assert "approver_answer" in caplog.text and "6.0" in caplog.text


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


async def test_a_report_with_only_the_token_is_not_taken(tmp_path, repo, duty, book):
    """The token says only that a program on this computer read the env file, not who runs it."""
    app = bridge_with(tmp_path, duty)
    args = call("p0")
    body = {"session_id": APPROVER, "tool": TOOL, "args_sha256": phone_hook.args_digest(args)}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=HOST) as c:
        sent = await c.post("/api/v1/approver/reports", json=body, headers=BEARER)
    assert sent.status_code in (404, 405)
    box(book, repo)
    assert answer(book, duty, args)["refused"] == approver.NOT_PROVEN


@pytest.fixture
def stranger():
    """A process that is not an ancestor of this test: a forger's stand-in for the approver."""
    child = subprocess.Popen(["sleep", "30"])
    yield child.pid
    child.kill()
    child.wait()


def under(pid):
    """A duty whose approver is the claude process `pid`, as its live file names it."""
    return lambda: (APPROVER, pid, lineage.start_ticks(pid))


def test_a_report_from_outside_the_approvers_process_tree_is_refused(book, repo, duty, stranger):
    duty.process = under(stranger)
    prompt = box(book, repo)
    args = call(prompt["id"])
    assert not duty.prove(os.getpid(), TOOL, phone_hook.args_digest(args))
    assert answer(book, duty, args)["refused"] == approver.NOT_PROVEN
    assert not duty.prove(None, TOOL, phone_hook.args_digest(args))  # the kernel did not say


def test_a_report_from_under_the_approvers_process_is_taken(book, repo, duty):
    duty.process = under(os.getppid())  # this test runs under its parent, as a hook runs under claude
    prompt = box(book, repo)
    args = call(prompt["id"])
    assert duty.prove(os.getpid(), TOOL, phone_hook.args_digest(args))
    assert answer(book, duty, args)["decided"] == "allow"


def test_a_dead_or_reused_approver_pid_proves_nothing():
    parent = os.getppid()
    assert lineage.descends(os.getpid(), parent, lineage.start_ticks(parent))
    assert not lineage.descends(os.getpid(), parent, "1")  # same pid, another start: reused
    gone = subprocess.Popen(["true"])
    gone.wait()
    assert lineage.start_ticks(gone.pid) is None
    assert not lineage.descends(os.getpid(), gone.pid, "1")


def test_without_the_kernels_word_on_the_sender_no_report_is_taken(monkeypatch):
    left, right = socket.socketpair()
    with left, right:
        assert lineage.peer_pid(left) == os.getpid()
        monkeypatch.delattr(socket, "SO_PEERCRED")
        assert lineage.peer_pid(left) is None


async def test_without_peer_credentials_nothing_listens(monkeypatch, duty, tmp_path):
    monkeypatch.delattr(socket, "SO_PEERCRED")
    assert await approver.serve_reports(duty, tmp_path / "r.sock") is None
    assert not (tmp_path / "r.sock").exists()


async def test_the_report_socket_is_bound_inside_a_folder_only_the_owner_can_open(duty, tmp_path):
    path = tmp_path / "approver" / "r.sock"
    path.parent.mkdir(mode=0o755)  # a folder others could look into, as a first start might find it
    server = await approver.serve_reports(duty, path)
    try:
        assert path.parent.stat().st_mode & 0o777 == 0o700
    finally:
        server.close()
        await server.wait_closed()


async def test_the_bridge_reads_who_sent_a_report_from_its_socket(book, repo, duty, stranger, tmp_path):
    path = tmp_path / "r.sock"
    server = await approver.serve_reports(duty, path)
    try:
        assert path.stat().st_mode & 0o777 == 0o600
        prompt = box(book, repo)
        args = call(prompt["id"])
        report = {"session_id": APPROVER, "tool": TOOL, "args_sha256": phone_hook.args_digest(args)}
        duty.process = under(stranger)
        await asyncio.to_thread(phone_hook.send_report, report, path)
        assert answer(book, duty, args)["refused"] == approver.NOT_PROVEN  # the claim alone is not enough
        duty.process = under(os.getpid())  # now this process is the approver's own tree
        await asyncio.to_thread(phone_hook.send_report, report, path)
        assert answer(book, duty, args)["decided"] == "allow"
    finally:
        server.close()
        await server.wait_closed()


@pytest.fixture
def forger():
    """A second process outside this test's tree: the one a planted session file names."""
    child = subprocess.Popen(["sleep", "30"])
    yield child.pid
    child.kill()
    child.wait()


def test_only_the_session_launched_as_the_approver_role_is_the_approver(tmp_path, stranger):
    (tmp_path / "home").mkdir()
    store = Store(tmp_path / "b.db")
    manager = SessionManager(store, project_root=tmp_path / "home", client_factory=FakeClaude())
    (tmp_path / "config.toml").write_text("", encoding="utf-8")
    repos = config.load(tmp_path / "config.toml", own_clone=None, env={"HOME": str(tmp_path)})
    duty = _duty(None, dataclasses.replace(repos, approver_on=True), manager)
    assert not duty.approver(APPROVER)  # no approver launched yet: nobody is it
    assert duty.process() is None
    store.set_setting("role:builder", {"name": "builder", "id": WORKER})
    pinned = {"name": "approver", "id": APPROVER, "pid": stranger, "started": lineage.start_ticks(stranger)}
    store.set_setting("role:approver", pinned)  # as launch_role records it
    assert duty.approver(APPROVER) and not duty.approver(WORKER)
    assert duty.process() == (APPROVER, stranger, lineage.start_ticks(stranger))


def test_a_session_file_written_after_launch_does_not_move_the_approver(tmp_path, stranger, forger):
    (tmp_path / "home").mkdir()
    store = Store(tmp_path / "b.db")
    manager = SessionManager(store, project_root=tmp_path / "home", client_factory=FakeClaude())
    (tmp_path / "config.toml").write_text("", encoding="utf-8")
    repos = config.load(tmp_path / "config.toml", own_clone=None, env={"HOME": str(tmp_path)})
    live = tmp_path / "live"
    live.mkdir()
    pinned = {"name": "approver", "id": APPROVER, "pid": stranger, "started": lineage.start_ticks(stranger)}
    store.set_setting("role:approver", pinned)
    planted = {"sessionId": APPROVER, "pid": forger, "procStart": lineage.start_ticks(forger)}
    (live / "0.json").write_text(json.dumps(planted), encoding="utf-8")  # sorts first, and its pid is alive
    duty = _duty(None, dataclasses.replace(repos, approver_on=True), manager)
    assert duty.process() == (APPROVER, stranger, lineage.start_ticks(stranger))
    assert not duty.prove(forger, TOOL, phone_hook.args_digest(call("p0")))
