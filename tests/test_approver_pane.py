"""The approver's window: the bridge opens it in tmux, takes its process from tmux's own answer,
and accepts it only once that process has become claude (approver-v1 v1.5, v1.7)."""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
import uuid

import pytest

from claude_voice import approver_pane, approver_token, lineage

pytestmark = [
    pytest.mark.skipif(sys.platform == "win32", reason="the approver needs /proc"),
    pytest.mark.skipif(shutil.which("tmux") is None, reason="needs tmux"),
]

SLEEP = os.path.realpath(shutil.which("sleep") or "/bin/sleep")
SH = os.path.realpath(shutil.which("sh") or "/bin/sh")


def _cmdline(pid: int) -> list[str]:
    with open(f"/proc/{pid}/cmdline", encoding="utf-8") as f:
        return f.read().split("\0")


@pytest.fixture
def tmux():
    """A tmux server of this test's own, so the owner's sessions are never touched."""
    name = f"cv-test-{uuid.uuid4().hex[:8]}"
    yield ["tmux", "-L", name]
    os.system(f"tmux -L {name} kill-server 2>/dev/null")  # noqa: S605 - a fixed name made above


async def test_the_approvers_process_is_taken_from_tmuxs_own_answer(tmux, tmp_path):
    pid = await approver_pane.open_window(tmux, "approver", str(tmp_path), [SLEEP, "30"])
    assert await approver_pane.became(pid, SLEEP, lineage.start_ticks(pid), 5)
    assert _cmdline(pid)[:2] == [SLEEP, "30"]  # no shell around it


async def test_the_approvers_window_counts_only_while_its_own_process_is_claude(tmux, tmp_path):
    pid = await approver_pane.open_window(tmux, "approver", str(tmp_path), [SLEEP, "30"])
    assert not await approver_pane.became(pid, "/usr/bin/not-claude", lineage.start_ticks(pid), 1)


async def test_a_wrapper_shell_in_the_window_is_refused_not_followed(tmux, tmp_path):
    pid = await approver_pane.open_window(tmux, "approver", str(tmp_path), [SH, "-c", f"{SLEEP} 30; true"])
    assert not await approver_pane.became(pid, SLEEP, lineage.start_ticks(pid), 1)


async def test_a_respawned_window_unpins_the_approver(tmux, tmp_path):
    pid = await approver_pane.open_window(tmux, "approver", str(tmp_path), [SLEEP, "30"])
    ticks = lineage.start_ticks(pid)
    keeper = approver_token.Keeper()
    keeper.mint(pid)
    token = keeper.hand(pid)
    assert keeper.valid(token)
    respawn = await asyncio.create_subprocess_exec(*tmux, "respawn-pane", "-k", "-t", "approver", SLEEP, "30")
    await respawn.wait()
    await asyncio.sleep(0.3)
    assert not keeper.valid(token)
    assert not await approver_pane.became(pid, SLEEP, ticks, 1)  # the pin was to the process now gone


async def test_the_window_holds_no_token_in_tmux(tmux, tmp_path):
    await approver_pane.open_window(tmux, "approver", str(tmp_path), [SLEEP, "30"])
    for scope in ([], ["-g"]):
        shown = await asyncio.create_subprocess_exec(
            *tmux, "show-environment", *scope, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
        )
        out, _ = await shown.communicate()
        assert b"CLAUDE_VOICE_APPROVER_TOKEN" not in out


def test_the_window_runs_the_fixed_starter_then_claude_by_its_absolute_path():
    argv = approver_pane.command("/py", "/abs/claude", "approver", 8811, {"hooks": 1}, "hi")
    assert argv[:4] == ["/py", "-I", "-m", "claude_voice.approver_start"]
    assert argv[4] == "/abs/claude"
    assert "--strict-mcp-config" in argv and argv[-1] == "hi"


async def test_the_approver_is_pinned_the_moment_its_window_opens(tmux, tmp_path):
    keeper = approver_token.Keeper()
    pid, ticks = await approver_pane.launch(tmux, keeper, "approver", str(tmp_path), [SLEEP, "30"], SLEEP, 5)
    assert ticks == lineage.start_ticks(pid)
    assert keeper.hand(pid)  # the token waits for this window's process, and only it


async def test_a_window_that_never_becomes_claude_leaves_no_token(tmux, tmp_path):
    keeper = approver_token.Keeper()
    with pytest.raises(RuntimeError, match="did not become claude"):
        await approver_pane.launch(tmux, keeper, "approver", str(tmp_path), [SH, "-c", f"{SLEEP} 30; true"], SLEEP, 1)
    assert keeper.hand(os.getpid()) is None
