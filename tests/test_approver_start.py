"""The fixed command in the approver's window: it asks the bridge for the approver's token over
the report socket, then becomes claude with the token in its environment only (approver-v1 v1.7)."""

from __future__ import annotations

import asyncio
import os
import sys

import pytest

from claude_voice import approver, approver_start, approver_token, boxed

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="the approver needs /proc")

CLAUDE = "/abs/claude"


@pytest.fixture
def became(monkeypatch):
    """What the starter turned into, instead of turning this test into it."""
    seen: list[tuple[str, list[str], dict[str, str]]] = []
    monkeypatch.setattr(approver_start.os, "execve", lambda path, args, env: seen.append((path, list(args), env)))
    return seen


def test_the_starter_gives_claude_the_token_in_its_environment_only(monkeypatch, became):
    monkeypatch.setattr(approver_start, "ask", lambda path: "the-token")
    approver_start.main([CLAUDE, "--tools", "", "--", "hello"])
    path, args, env = became[0]
    assert path == CLAUDE and args == [CLAUDE, "--tools", "", "--", "hello"]
    assert env[boxed.TOKEN_VAR] == "the-token"
    assert "the-token" not in " ".join(args)


def test_the_starter_runs_claude_only_by_an_absolute_path(monkeypatch, became):
    monkeypatch.setattr(approver_start, "ask", lambda path: "the-token")
    assert approver_start.main(["claude", "--", "hello"]) != 0
    assert approver_start.main([]) != 0
    assert not became


def test_the_starter_prints_no_token_and_no_environment(monkeypatch, capsys, became):
    monkeypatch.setenv("SOME_SECRET_OF_THE_OWNER", "do-not-print-me")
    monkeypatch.setattr(approver_start, "ask", lambda path: None)
    assert approver_start.main([CLAUDE]) != 0
    monkeypatch.setattr(approver_start, "ask", lambda path: "the-token")

    def fails(path, args, env):
        raise OSError(f"cannot run {path} with {env}")

    monkeypatch.setattr(approver_start.os, "execve", fails)
    assert approver_start.main([CLAUDE]) != 0
    out = capsys.readouterr()
    assert "do-not-print-me" not in out.out + out.err
    assert "the-token" not in out.out + out.err
    assert not became


async def test_the_starter_gets_the_token_the_bridge_holds_for_its_window(tmp_path):
    keeper = approver_token.Keeper()
    keeper.mint(os.getpid())  # this test process stands in for the window
    duty = approver.Duty(on=False, role=lambda s: True, approver=lambda s: True, log=tmp_path / "log.jsonl")
    path = tmp_path / "approver" / "r.sock"
    server = await approver.serve_reports(duty, path, keeper)
    try:
        token = await asyncio.to_thread(approver_start.ask, path)
        again = await asyncio.to_thread(approver_start.ask, path)
    finally:
        server.close()
        await server.wait_closed()
    assert token and keeper.valid(token)
    assert again is None
