"""A question left waiting, in the bridge: the manager's line, the voice's news, no typing."""

import json
import subprocess
import sys

import pytest
from fakes import FakeClaude
from mcp.client import Client

from claude_voice import stalled
from claude_voice.presence import Desktop, Presence
from claude_voice.server import build_server
from claude_voice.sessions import SessionManager
from claude_voice.store import Store

ORG = """
[programme]
code = "acme"
name = "Acme Robots"
stalled_prompt_text = true

[components.arm]
name = "Arm"
folders = ["."]

[roles.manager]
name = "Manager"
sessions = ["acme-boss"]
"""

PANE = """\
╭──────────────────────────────────────────╮
│ Bash command                             │
│                                          │
│   uv run pytest -q                       │
│                                          │
│ Do you want to proceed?                  │
│ ❯ 1. Yes                                 │
│   2. No                                  │
╰──────────────────────────────────────────╯
"""
NOW = 1_000_000.0
LINE = (
    "fix-gripper has waited 6 minutes on a question: 'Bash command uv run pytest -q Do you want to proceed?'."
    " Options: 1 Yes, 2 No. It's in its window. Its manager has been told."
)


class Clock:
    def __init__(self):
        self.now = NOW

    def __call__(self):
        return self.now


class Delivered:
    def __init__(self):
        self.sent = []

    async def __call__(self, name, text):
        self.sent.append((name, text))
        return {"delivered": True, "detail": ""}


class FakeTmux:
    """tmux as the bridge sees it: one pane per session, showing the prompt."""

    def __init__(self, pids):
        self.pids, self.commands = pids, []

    def __call__(self, command):
        self.commands.append(command)
        if command[:2] == ["tmux", "list-panes"]:
            return "".join(f"{pid} work %{i}\n" for i, pid in enumerate(self.pids))
        if command[:2] == ["tmux", "capture-pane"]:
            return PANE
        return ""


@pytest.fixture
def pids():
    started = [subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"]) for _ in range(2)]
    yield [p.pid for p in started]
    for p in started:
        p.kill()
        p.wait()


@pytest.fixture
def bridge(tmp_path, pids):
    return make_bridge(tmp_path, pids, Delivered())


def make_bridge(tmp_path, pids, deliver, org_text=ORG, waiting_for="permission prompt"):
    root = tmp_path / "src"
    (root / "acme" / ".claude").mkdir(parents=True)
    (root / "acme" / ".claude" / "org.toml").write_text(org_text)
    live = tmp_path / "live"
    live.mkdir()
    waits = {"acme-boss": ("idle", NOW), "fix-gripper": ("waiting", NOW - 6 * 60)}
    for pid, (name, (status, since)) in zip(pids, waits.items(), strict=True):
        record = {"pid": pid, "sessionId": f"{name}-id", "cwd": str(root / "acme"), "name": name}
        record |= {"status": status, "statusUpdatedAt": since * 1000, "waitingFor": waiting_for}
        (live / f"{pid}.json").write_text(json.dumps(record))
    clock = Clock()
    tmux = FakeTmux(pids)
    m = SessionManager(Store(tmp_path / "b.db"), project_root=root, client_factory=FakeClaude(), clock=clock)
    server = build_server(
        m,
        conversations=lambda **k: [],
        live_dir=live,
        read_transcript=lambda s, d: [],
        deliver=deliver,
        org_folders=[],
        presence=Presence(desktop=lambda: Desktop(locked=True, idle_seconds=None)),
        stalled_watch=stalled.Watch(5, clock=clock, hour=lambda t: 12),
        stalled_tmux=tmux,
        manager_delivery_seconds=0.05,
    )
    return server, deliver, tmux, clock


async def call(server, name, **args):
    async with Client(server) as c:
        return (await c.call_tool(name, args)).structured_content


async def let_deliveries_finish():
    import asyncio

    for _ in range(5):
        await asyncio.sleep(0)


async def test_the_manager_and_the_voice_hear_once_of_a_question_left_waiting(bridge):
    server, deliver, _, clock = bridge
    cursor = (await call(server, "whats_new"))["cursor"]
    await server.stalled_check()
    await let_deliveries_finish()
    clock.now += 60
    await server.stalled_check()
    await let_deliveries_finish()
    assert deliver.sent == [("acme-boss", LINE)]
    news = (await call(server, "whats_new", cursor=cursor))["events"]
    assert [e["text"] for e in news if e["kind"] == "stalled_prompt"] == [LINE]


async def test_a_waiting_session_is_never_typed_into(bridge):
    server, _, tmux, clock = bridge
    for _ in range(3):
        await server.stalled_check()
        clock.now += 60
    assert ["tmux", "capture-pane", "-p", "-J", "-t", "%1"] in tmux.commands
    assert not any("send-keys" in part for command in tmux.commands for part in command)


class Stuck:
    """A courier that takes the message and never answers."""

    async def __call__(self, name, text):
        import asyncio

        await asyncio.Event().wait()


async def test_a_line_to_a_manager_about_a_waiting_question_never_holds_up_the_watch(tmp_path, pids):
    import asyncio

    server, _, _, _ = make_bridge(tmp_path, pids, Stuck())
    await asyncio.wait_for(server.stalled_check(), 5)


async def test_a_delivery_about_a_waiting_question_gives_up_after_its_time(tmp_path, pids):
    import asyncio

    server, _, _, _ = make_bridge(tmp_path, pids, Stuck())
    await server.stalled_check()
    await asyncio.sleep(0.3)
    assert server.deliveries_in_flight == set()


async def test_a_held_question_is_answerable_by_voice_when_its_text_is_not_said(tmp_path, pids):
    import asyncio

    deliver = Delivered()
    quiet = ORG.replace("stalled_prompt_text = true\n", "")
    server, _, _, _ = make_bridge(tmp_path, pids, deliver, org_text=quiet, waiting_for="input needed")
    options = [{"label": "Red", "description": ""}, {"label": "Blue", "description": ""}]
    question = {"text": "Which colour?", "header": "Colour", "options": options, "multi": False}
    prompt = {"id": "q1", "session": "fix-gripper-id", "kind": "question", "answer": None}
    prompt |= {"done": asyncio.Event(), "asked_at": "2026-11-18T12:00:00+00:00", "question": question}
    server.prompt_book.add(prompt)
    prompt["hook"]["waiting"] = 1  # a hook waits on it
    cursor = (await call(server, "whats_new"))["cursor"]
    await server.stalled_check()
    await let_deliveries_finish()
    news = (await call(server, "whats_new", cursor=cursor))["events"]
    assert [e["text"] for e in news if e["kind"] == "stalled_prompt"] == ["fix-gripper has a question waiting."]
    assert "Which colour?" in deliver.sent[0][1] and "It's in its window" not in deliver.sent[0][1]
    asked = (await call(server, "list_pending_approvals"))["questions"]
    assert [(q["id"], [o["label"] for o in q["options"]]) for q in asked] == [("q1", ["Red", "Blue"])]
