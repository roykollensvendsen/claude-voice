"""Starting the approver role: its own tmux window, the fixed starter, and the process pinned from
tmux's answer, never from a session file (approver-v1, v1.5 and v1.7)."""

from __future__ import annotations

import json
import os
import sys

import pytest
from fakes import FakeClaude
from mcp.client import Client

from claude_voice import lineage
from claude_voice.server import build_server
from claude_voice.sessions import SessionManager
from claude_voice.store import Store

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="the approver needs /proc and tmux")

PID = os.getpid()  # this test process stands in for the approver's window
CLAUDE = "/opt/claude/bin/claude"
REAL = os.path.realpath(CLAUDE)


class Window:
    """Stands in for opening the tmux window: records the command and pins as the real one does."""

    def __init__(self, live, rows):
        self.live, self.rows, self.calls = live, rows, []

    async def __call__(self, tmux, keeper, name, cwd, argv, claude, wait):
        self.calls.append({"tmux": tmux, "name": name, "cwd": cwd, "argv": argv, "claude": claude})
        keeper.mint(PID)
        for i, row in enumerate(self.rows):
            (self.live / f"{i}.json").write_text(json.dumps({"name": name, "cwd": cwd, **row}), encoding="utf-8")
        return PID, lineage.start_ticks(PID)


@pytest.fixture
def world(tmp_path):
    home = tmp_path / "home"
    folder = home / "approver"
    folder.mkdir(parents=True)
    live = tmp_path / "live"
    live.mkdir()
    table = tmp_path / "roles.toml"
    table.write_text(f"[roles.approver]\nfolder = '{folder}'\n", encoding="utf-8")
    return home, folder, live, table


def bridge(tmp_path, world, window, programs):
    home, _, live, table = world
    m = SessionManager(Store(tmp_path / "b.db"), project_root=home, client_factory=FakeClaude())
    return build_server(
        m,
        conversations=lambda **k: [],
        live_dir=live,
        find_program=programs.get,
        start_seconds=1.0,
        roles_file=table,
        approver_window=window,
    )


async def test_the_approver_starts_in_its_own_window_through_the_fixed_starter(tmp_path, world):
    window = Window(world[2], [{"pid": PID, "sessionId": "sid-approver"}])
    programs = {"tmux": "/usr/bin/tmux", "claude": CLAUDE}
    async with Client(bridge(tmp_path, world, window, programs)) as c:
        out = (await c.call_tool("start_role", {"role": "approver"})).structured_content
    argv = window.calls[0]["argv"]
    assert argv[:4] == [sys.executable, "-I", "-m", "claude_voice.approver_start"]
    assert argv[4] == window.calls[0]["claude"] == REAL
    assert "--strict-mcp-config" in argv and argv[argv.index("--tools") + 1] == ""
    assert window.calls[0]["cwd"] == str(world[1])
    assert out["role"] == "approver" and out["claude_session_id"] == "sid-approver"


async def test_the_approvers_process_is_taken_from_its_window_not_a_session_file(tmp_path, world):
    """A row naming the approver but another process is never the approver's."""
    planted = {"pid": 1, "sessionId": "sid-planted"}
    window = Window(world[2], [planted, {"pid": PID, "sessionId": "sid-approver"}])
    programs = {"tmux": "/usr/bin/tmux", "claude": CLAUDE}
    async with Client(bridge(tmp_path, world, window, programs)) as c:
        await c.call_tool("start_role", {"role": "approver"})
    held = Store(tmp_path / "b.db").get_setting("role:approver")
    assert held["id"] == "sid-approver"
    assert held["pid"] == PID and held["started"] == lineage.start_ticks(PID)
    assert held["folder"] == str(world[1])  # where its transcript is looked for


async def test_the_approver_cannot_start_without_tmux(tmp_path, world):
    window = Window(world[2], [{"pid": PID, "sessionId": "sid-approver"}])
    async with Client(bridge(tmp_path, world, window, {"claude": CLAUDE})) as c:
        out = await c.call_tool("start_role", {"role": "approver"})
    assert out.is_error and "tmux" in out.content[0].text
    assert not window.calls
    assert Store(tmp_path / "b.db").get_setting("role:approver") is None
