"""fleet_recap: every session in one call, each with one line on what it is doing."""

import json
import os

import pytest
from claude_agent_sdk import project_key_for_directory
from fakes import FakeClaude, result
from mcp.client import Client

from claude_voice.server import build_server
from claude_voice.sessions import SessionManager
from claude_voice.store import Store
from claude_voice.transcripts import doing_line

PID = os.getpid()


@pytest.fixture
def world(tmp_path):
    (tmp_path / "home" / "billing").mkdir(parents=True)
    (tmp_path / "home" / "export").mkdir()
    (tmp_path / "live").mkdir()
    return tmp_path


def running(world, name, project, sid, status="busy", kind="interactive", pid=PID):
    (world / "live" / f"{name}.json").write_text(
        json.dumps(
            {
                "pid": pid,
                "sessionId": sid,
                "cwd": str(world / "home" / project),
                "name": name,
                "status": status,
                "kind": kind,
                "waitingFor": "input needed" if status == "waiting" else None,
            }
        )
    )


def transcript(world, project, sid, entries):
    path = world / "projects" / project_key_for_directory(world / "home" / project) / f"{sid}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(e) + "\n" for e in entries))


def says(text):
    return {"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}}


def asks(question, *options):
    return {
        "type": "assistant",
        "message": {
            "content": [
                {
                    "type": "tool_use",
                    "id": "q1",
                    "name": "AskUserQuestion",
                    "input": {"questions": [{"question": question, "options": [{"label": o} for o in options]}]},
                }
            ]
        },
    }


def test_doing_is_the_first_sentence_of_the_latest_words_without_markup(world):
    transcript(world, "billing", "s1", [says("old"), says("**Fixing** the `export` totals. Then tests. | a |")])
    line = doing_line("s1", str(world / "home" / "billing"), world / "projects")
    assert line == "Fixing the export totals."


def test_doing_is_kept_to_one_short_line(world):
    transcript(world, "billing", "s1", [says("word " * 100)])
    assert len(doing_line("s1", str(world / "home" / "billing"), world / "projects")) <= 160


def test_doing_names_an_open_question(world):
    transcript(world, "billing", "s1", [says("Tests pass."), asks("Merge it now?", "Ja", "Nei")])
    assert doing_line("s1", str(world / "home" / "billing"), world / "projects") == "venter på valg: Merge it now?"


async def test_one_call_covers_running_and_bridge_sessions(world):
    running(world, "billing-1", "billing", "s1", status="busy")
    running(world, "export-1", "export", "s2", status="waiting", pid=PID)
    transcript(world, "billing", "s1", [says("Fixing the export totals. More detail.")])
    transcript(world, "export", "s2", [asks("Merge it now?", "Ja", "Nei")])
    store = Store(world / "b.db")
    m = SessionManager(store, project_root=world / "home", client_factory=FakeClaude([result("Report done.", "c")]))
    srv = build_server(m, conversations=lambda **k: [], live_dir=world / "live", projects_dir=world / "projects")
    async with Client(srv) as c:
        s = (await c.call_tool("create_session", {"project": "billing", "label": "report"})).structured_content
        await c.call_tool("send_task", {"session_id": s["id"], "prompt": "go"})
        await m.wait(s["id"])
        fleet = (await c.call_tool("fleet_recap", {})).structured_content
    rows = {r["name"]: r for r in fleet["sessions"]}
    assert rows["billing-1"]["doing"] == "Fixing the export totals."
    assert rows["billing-1"]["kind"] == "terminal" and rows["billing-1"]["status"] == "busy"
    assert rows["export-1"]["doing"] == "venter på valg: Merge it now?"
    assert rows["export-1"]["question"]["options"] == ["Ja", "Nei"]
    assert rows["report"]["kind"] == "bridge-session"
    assert rows["report"]["doing"] == "Report done."
    assert set(rows["billing-1"]) >= {"id", "name", "kind", "project", "status", "doing", "minutes_since_update"}


def test_doing_leaves_out_web_addresses(world):
    transcript(world, "billing", "s1", [says("PR 23 is open: https://github.com/x/y/pull/23 and waits.")])
    assert doing_line("s1", str(world / "home" / "billing"), world / "projects") == "PR 23 is open: and waits."
