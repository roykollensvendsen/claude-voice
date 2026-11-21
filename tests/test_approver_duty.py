"""The approver on duty: which boxes wait for it, what it may answer, and what it leaves in its log."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import httpx
import pytest
from fakes import FakeClaude

from claude_voice import answers, api, approver, config, phone_hook, presence, prompts
from claude_voice.oauth import OAuthProvider
from claude_voice.server import build_app, build_server
from claude_voice.sessions import SessionManager
from claude_voice.store import Store

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX paths and file modes")

ROLE = "role-session-1"
SECRET_WORD = "made-up-secret-value"
SECRET = "s3cret-token-for-the-duty-tests-xx"
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
    (tmp_path / "repo" / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
    return tmp_path / "repo"


@pytest.fixture
def duty(tmp_path, clock):
    return approver.Duty(on=True, started=lambda s: s == ROLE, clock=clock, log=tmp_path / "approver" / "log.jsonl")


@pytest.fixture
def book(clock):
    return prompts.Book(clock=clock)


def box(book, repo, command="uv run pytest", lines=(), session=ROLE, waiting=True):
    prompt = {
        "id": f"p{len(book.items)}",
        "session": session,
        "kind": "approval",
        "asked_at": "2026-11-21T12:00:00+00:00",
        "answer": None,
        "done": asyncio.Event(),
        "tool": "Bash",
        "input": command,
        "cut": False,
        "raw_input": {"command": command},
        "cwd": str(repo),
        "routed": {"to": "owner", "line": None, "lines": list(lines), "shape": False},
    }
    book.add(prompt)
    prompt["hook"]["waiting"] = 1 if waiting else 0
    return prompt


def published():
    """What the box's answer published, and how the approver's answer settles it."""
    seen = []
    return seen, lambda prompt, body: answers.settle(prompt, body, "approver", lambda *event: seen.append(event))


def log_lines(duty):
    return [json.loads(line) for line in duty.log.read_text(encoding="utf-8").splitlines()]


def test_a_box_waits_for_the_approver_only_while_it_is_on_duty(book, repo, duty, clock, tmp_path):
    prompt = box(book, repo)
    assert not duty.covers(prompt)  # it has not checked in yet
    duty.check_in()
    assert duty.covers(prompt)
    clock.now += approver.CHECK_IN + 1
    assert not duty.covers(prompt)  # a silent approver gives today's behaviour back
    duty.check_in()
    assert not duty.covers(box(book, repo, session="owners-own-terminal"))  # f2: roles only
    assert not duty.covers({**prompt, "kind": "question"})
    off = approver.Duty(on=False, started=lambda s: True, clock=clock, log=tmp_path / "off.jsonl")
    off.check_in()
    assert not off.covers(prompt)


def test_the_approver_sees_each_open_box_and_whether_it_may_answer_it(book, repo, duty):
    box(book, repo)
    box(book, repo, command="git push origin main")
    box(book, repo, waiting=False)
    listed = approver.boxes(book, duty, {ROLE: "builder"})
    assert [(b["session_name"], b["input"], b["answerable"]) for b in listed] == [
        ("builder", "uv run pytest", True),
        ("builder", "git push origin main", False),
    ]
    assert duty.on_duty()  # looking is the check-in


def test_the_approver_is_refused_while_switched_off(book, repo, tmp_path, clock):
    off = approver.Duty(on=False, started=lambda s: True, clock=clock, log=tmp_path / "off.jsonl")
    prompt = box(book, repo)
    assert "refused" in approver.boxes(book, off, {})
    assert "refused" in approver.answer(book, off, prompt["id"], "allow", "ok", published()[1])
    assert prompt["answer"] is None


def test_an_approver_answer_settles_an_answerable_box_once_and_never_for_always(book, repo, duty):
    prompt = box(book, repo)
    done = approver.answer(book, duty, prompt["id"], "allow", "the session's own tests", published()[1])
    assert done["decided"] == "allow" and prompt["answer"]["decision"] == "allow"
    hook = json.dumps(phone_hook._approval_answer(prompt["answer"]))
    assert "updatedPermissions" not in hook and "always" not in hook
    again = approver.answer(book, duty, prompt["id"], "deny", "twice", published()[1])
    assert again["refused"] == approver.TOO_LATE


def test_the_approver_never_answers_a_box_the_owner_holds(book, repo, duty):
    owners = box(book, repo, lines=(1,))
    unsorted = box(book, repo)
    unsorted.pop("routed")
    for prompt in (owners, unsorted):
        for decision in ("allow", "deny"):
            assert "refused" in approver.answer(book, duty, prompt["id"], decision, "x", published()[1])
        assert prompt["answer"] is None


def test_an_answer_after_the_box_fell_back_to_the_screen_is_refused_as_too_late(book, repo, duty):
    prompt = box(book, repo)
    book.close(prompt["id"], "timeout")
    assert approver.answer(book, duty, prompt["id"], "allow", "x", published()[1])["refused"] == approver.TOO_LATE
    assert prompt["answer"] is None
    assert log_lines(duty)[-1]["decision"] == "late"
    unwatched = box(book, repo, waiting=False)  # still listed, but its hook no longer waits
    assert approver.answer(book, duty, unwatched["id"], "allow", "x", published()[1])["refused"] == approver.TOO_LATE
    assert unwatched["answer"] is None


def test_the_timeout_and_an_answer_race_and_exactly_one_wins(book, repo, duty):
    first = box(book, repo)
    seen, publish = published()
    approver.answer(book, duty, first["id"], "allow", "x", publish)
    closed = book.close(first["id"], "timeout")
    assert closed is first and closed["answer"]["decision"] == "allow"  # the answer won; the hook takes it
    second = box(book, repo)
    book.close(second["id"], "timeout")
    assert "refused" in approver.answer(book, duty, second["id"], "allow", "x", publish)  # the timeout won
    assert second["answer"] is None


def test_the_approver_log_keeps_each_decision_without_the_input_text(book, repo, duty):
    prompt = box(book, repo, command=f"uv run pytest -k {SECRET_WORD}")
    approver.answer(book, duty, prompt["id"], "deny", "not now", published()[1])
    [line] = log_lines(duty)
    fields = {"at", "session", "folder", "tool", "input_sha256", "lines", "shape", "decision", "by", "reason"}
    assert set(line) == fields
    assert line["decision"] == "deny" and line["by"] == "approver" and line["reason"] == "not now"
    assert SECRET_WORD not in duty.log.read_text(encoding="utf-8")
    assert duty.log.stat().st_mode & 0o777 == 0o600


def test_a_box_the_approver_saw_that_times_out_is_logged_as_a_timeout(book, repo, duty):
    prompt = box(book, repo)
    duty.closed(prompt, "timeout")
    duty.closed(box(book, repo), "screen")  # only a timeout is the approver's to log
    assert [(line["decision"], line["by"]) for line in log_lines(duty)] == [("timeout", "screen")]


def test_a_scratchpad_box_is_judged_inside_the_sessions_own_scratchpad(book, repo, duty, monkeypatch, tmp_path):
    monkeypatch.setattr(approver.session_hook, "_tmp_root", lambda: tmp_path / "tmp")
    prompt = box(book, repo)
    place = approver.place_of(prompt)
    assert place is not None and place.repo == Path(repo)
    assert place.scratchpad is not None and place.scratchpad.parts[-2:] == (ROLE, "scratchpad")
    assert str(place.scratchpad).startswith(str(tmp_path / "tmp"))


@pytest.mark.parametrize("text", ["", "[approver]\n", "[approver]\non = false\n"])
def test_the_approver_is_off_unless_the_config_says_true(tmp_path, text):
    file = tmp_path / "config.toml"
    file.write_text(text, encoding="utf-8")
    assert config.load(file, own_clone=None, env={"HOME": str(tmp_path)}).approver_on is False
    file.write_text('[approver]\non = "yes"\n', encoding="utf-8")
    with pytest.raises(config.ConfigError, match="not true or false"):
        config.load(file, own_clone=None, env={"HOME": str(tmp_path)})


def test_the_approver_cannot_be_switched_on_before_its_caller_check(tmp_path):
    """Until the bridge checks who calls the approver's tools, any session could answer its own box."""
    file = tmp_path / "config.toml"
    file.write_text("[approver]\non = true\n", encoding="utf-8")
    with pytest.raises(config.ConfigError, match="caller check"):
        config.load(file, own_clone=None, env={"HOME": str(tmp_path)})


def at_the_desk_bridge(tmp_path, duty):
    (tmp_path / "home").mkdir(exist_ok=True)
    store = Store(tmp_path / "b.db")
    oauth = OAuthProvider(store, SECRET, HOST, static_token=SECRET)
    m = SessionManager(store, project_root=tmp_path / "home", client_factory=FakeClaude())
    seen = presence.Presence(desktop=lambda: presence.Desktop(locked=False, idle_seconds=0.0))
    seen.cache = 0
    server = build_server(
        m, oauth=oauth, conversations=lambda **k: [], live_dir=tmp_path / "live", presence=seen, duty=duty
    )
    return build_app(server), server


async def test_a_box_waits_for_the_approver_even_with_the_owner_at_the_laptop(tmp_path, repo, duty, monkeypatch):
    monkeypatch.setattr(api, "RECHECK", 0.05)
    app, server = at_the_desk_bridge(tmp_path, duty)
    duty.check_in()
    body = {"session_id": ROLE, "kind": "approval", "tool": "Bash", "input": {"command": "ls"}, "cwd": str(repo)}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=HOST) as c:
        opened = (await c.post("/api/v1/prompts", json=body, headers=BEARER)).json()
        owners = (await c.post("/api/v1/prompts", json={**body, "session_id": "own"}, headers=BEARER)).json()
        waited = await c.get(f"/api/v1/prompts/{opened['id']}/wait?seconds=0.2", headers=BEARER)
        listed = (await c.get("/api/v1/prompts", headers=BEARER)).json()["prompts"]
    assert opened["wait"] is True and owners["wait"] is False
    assert waited.status_code == 204  # still waiting for the approver, not back on the screen
    assert server.prompt_book.items[opened["id"]]["raw_input"] == {"command": "ls"}
    assert all("raw_input" not in p and "cwd" not in p for p in listed)
