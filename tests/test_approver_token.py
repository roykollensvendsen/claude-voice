"""The approver's own token: minted at launch, handed once to the pane's own process, valid only
while that process lives, and kept only as a hash (approver-v1 v1.7)."""

from __future__ import annotations

import asyncio
import hashlib
import os
import socket
import subprocess
import sys

import pytest

from claude_voice import approver, approver_token

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="the approver needs /proc")


@pytest.fixture
def pane():
    """A real process standing in for the approver's pane."""
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    yield child.pid
    child.kill()
    child.wait()


@pytest.fixture
def other():
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    yield child.pid
    child.kill()
    child.wait()


def _handed(pane: int) -> tuple[approver_token.Keeper, str]:
    keeper = approver_token.Keeper()
    keeper.mint(pane)
    token = keeper.hand(pane)
    assert token
    return keeper, token


def test_the_approvers_token_goes_only_to_its_own_pane(pane, other):
    keeper = approver_token.Keeper()
    keeper.mint(pane)
    assert keeper.hand(other) is None
    assert keeper.hand(None) is None
    assert keeper.hand(pane)


def test_the_approvers_token_is_handed_over_once(pane):
    keeper, _ = _handed(pane)
    assert keeper.hand(pane) is None


def test_the_approvers_token_opens_while_its_pane_lives(pane):
    keeper, token = _handed(pane)
    assert keeper.valid(token)
    assert not keeper.valid(token + "x")
    assert not keeper.valid("")


def test_the_approvers_token_dies_with_its_pane():
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    keeper, token = _handed(child.pid)
    child.kill()
    child.wait()
    assert not keeper.valid(token)


def test_a_new_launch_revokes_the_old_token(pane, other):
    keeper, token = _handed(pane)
    keeper.mint(other)
    assert not keeper.valid(token)


def test_a_reused_pane_pid_does_not_keep_the_token(pane, monkeypatch):
    keeper, token = _handed(pane)
    monkeypatch.setattr(approver_token.lineage, "start_ticks", lambda pid: "1")  # same pid, another start
    assert not keeper.valid(token)


def test_the_bridge_keeps_only_the_tokens_hash(pane):
    keeper, token = _handed(pane)
    held = [str(v) for v in vars(keeper).values()]
    assert token not in held
    assert hashlib.sha256(token.encode()).hexdigest() in held


def test_a_token_never_handed_over_opens_nothing(pane):
    keeper = approver_token.Keeper()
    keeper.mint(pane)
    assert not keeper.valid("anything")
    keeper.revoke()
    assert keeper.hand(pane) is None


# -- the token is handed over the report socket ------------------------------------------------


def _ask(path, line: bytes) -> bytes:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(2)
        s.connect(str(path))
        s.sendall(line)
        return s.recv(256)


async def _served(tmp_path, keeper, on=False):
    duty = approver.Duty(on=on, role=lambda s: True, approver=lambda s: True, log=tmp_path / "log.jsonl")
    path = tmp_path / "approver" / "r.sock"
    return duty, path, await approver.serve_reports(duty, path, keeper)


async def test_the_approvers_window_gets_its_token_over_the_report_socket(tmp_path):
    keeper = approver_token.Keeper()
    keeper.mint(os.getpid())  # this test process stands in for the pane
    _, path, server = await _served(tmp_path, keeper)
    try:
        token = (await asyncio.to_thread(_ask, path, b'{"ask": "token"}\n')).decode().strip()
        again = (await asyncio.to_thread(_ask, path, b'{"ask": "token"}\n')).decode().strip()
    finally:
        server.close()
        await server.wait_closed()
    assert keeper.valid(token)
    assert again == "no"


async def test_a_token_ask_and_a_report_never_stand_in_for_each_other(tmp_path):
    keeper = approver_token.Keeper()
    keeper.mint(os.getpid())
    duty, path, server = await _served(tmp_path, keeper, on=True)
    duty.process = lambda: ("approver", os.getpid(), approver_token.lineage.start_ticks(os.getpid()))
    report = b'{"session_id": "s", "tool": "mcp__x__approver_boxes", "args_sha256": "d"}\n'
    try:
        said = await asyncio.to_thread(_ask, path, report)
        assert said.strip() == b"ok"  # a report never gets a token
        assert len(duty.reports) == 1
        token = (await asyncio.to_thread(_ask, path, b'{"ask": "token"}\n')).decode().strip()
        assert keeper.valid(token)  # the report did not use the token up
        assert len(duty.reports) == 1  # and the ask did not count as a report
    finally:
        server.close()
        await server.wait_closed()
