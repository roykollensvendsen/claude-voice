"""The voice can follow an update of the bridge: which step it is on, how it ended, and whether it
was rolled back (#431). It reads the log the updater already writes, and changes nothing."""

import json
import os
from datetime import datetime

from fakes import FakeClaude
from mcp.client import Client

from claude_voice import updater
from claude_voice.oauth import OAuthProvider
from claude_voice.server import build_server
from claude_voice.sessions import SessionManager
from claude_voice.store import Store

SECRET = "s3cret-token-for-the-status-tests-x"
STARTED = "2026-11-23T21:59:16+00:00"


def clock(at):
    return datetime.fromisoformat(at).astimezone().strftime("%H:%M")


def step(name, ok=True, detail=""):
    return {"step": name, "ok": ok, "detail": detail, "at": STARTED}


#: Tonight's case: the new version's tests failed, so it was rolled back without a restart.
ROLLED_BACK = {
    "running": False,
    "pid": 1,
    "started_at": STARTED,
    "finished_at": "2026-11-23T22:03:26+00:00",
    "steps": [
        step("check"),
        step("fetch"),
        step("pull"),
        step("install"),
        step("test", False, "\nFAILED tests/test_x.py::test_y - AssertionError\n1 failed, 5344 passed in 248s"),
        step("rollback", True, "back on b9b17e1"),
    ],
    "from": "b9b17e1",
    "to": "8d7de36",
    "result": "rolled_back",
    "detail": "The new version failed to test; the old one is kept.",
}


def test_no_update_yet_is_said_plainly():
    out = updater.status(None, running=False)
    assert out["state"] == "none" and "No update" in out["say"]


def test_a_rolled_back_update_names_the_step_that_failed_and_why_in_one_line():
    out = updater.status(ROLLED_BACK, running=False)
    assert out["state"] == "rolled_back" and out["rolled_back"] is True
    assert out["failed_step"] == "test"
    assert out["problem"] == "FAILED tests/test_x.py::test_y - AssertionError"
    assert "rolled back" in out["say"] and out["problem"] in out["say"] and clock(STARTED) in out["say"]


def test_a_finished_update_says_how_it_ended():
    done = {**ROLLED_BACK, "steps": [step("check"), step("done")], "result": "updated", "detail": "Updated to 8d7de36."}
    out = updater.status(done, running=False)
    assert out["state"] == "updated" and out["rolled_back"] is False and "failed_step" not in out
    assert "Updated to 8d7de36." in out["say"]


def test_a_running_update_says_its_step_and_when_it_started():
    running = {"running": True, "pid": 1, "started_at": STARTED, "steps": [step("check"), step("test")]}
    out = updater.status(running, running=True)
    assert out["state"] == "running" and out["step"] == "test"
    assert "test" in out["say"] and clock(STARTED) in out["say"]


def test_an_update_whose_process_died_is_said_to_have_stopped_without_finishing():
    died = {"running": True, "pid": 1, "started_at": STARTED, "steps": [step("check"), step("test")]}
    out = updater.status(died, running=False)
    assert out["state"] == "interrupted" and "without finishing" in out["say"] and "test" in out["say"]


def test_a_failed_step_with_nothing_to_say_is_named_without_an_empty_reason():
    bare = {**ROLLED_BACK, "steps": [step("check"), step("install", False, "")], "detail": ""}
    said = updater.status(bare, running=False)["say"]
    assert said.endswith("rolled back at the install step.") and ": ." not in said


def test_a_problem_is_one_short_line():
    long = {**ROLLED_BACK, "steps": [step("install", False, "x" * 900)]}
    assert len(updater.status(long, running=False)["problem"]) <= 200


def bridge(tmp_path):
    (tmp_path / "home").mkdir(exist_ok=True)
    store = Store(tmp_path / "b.db")
    return build_server(
        SessionManager(store, project_root=tmp_path / "home", client_factory=FakeClaude()),
        oauth=OAuthProvider(store, SECRET, "http://127.0.0.1:8811", static_token=SECRET),
        conversations=lambda **k: [],
        live_dir=tmp_path / "live",
        update_log=tmp_path / "update.json",
        launch_update=lambda repo, log: None,
        bridge_folder=tmp_path,
    )


async def test_the_voice_asks_how_the_last_update_went(tmp_path):
    (tmp_path / "update.json").write_text(json.dumps(ROLLED_BACK), encoding="utf-8")
    async with Client(bridge(tmp_path)) as mcp:
        out = (await mcp.call_tool("update_status", {})).structured_content
    assert out["state"] == "rolled_back" and out["failed_step"] == "test"
    assert (tmp_path / "update.json").read_text(encoding="utf-8") == json.dumps(ROLLED_BACK)


async def test_a_second_update_names_the_step_the_running_one_is_on_and_when_it_started(tmp_path):
    running = {"running": True, "pid": os.getpid(), "started_at": STARTED, "steps": [step("check"), step("test")]}
    (tmp_path / "update.json").write_text(json.dumps(running), encoding="utf-8")
    async with Client(bridge(tmp_path)) as mcp:
        refused = await mcp.call_tool("update_bridge", {})
    said = refused.content[0].text
    assert refused.is_error and "already running" in said and "test step" in said and clock(STARTED) in said
