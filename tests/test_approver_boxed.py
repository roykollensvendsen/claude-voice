"""The approver starts boxed in: only the bridge's two approver tools, no tools of its own,
none of the owner's settings files, and only the bridge's own hook (approver-v1 v1.7)."""

from __future__ import annotations

import json

from claude_voice import boxed

HOOKS = {"PreToolUse": [{"matcher": "x", "hooks": [{"type": "command", "command": "/py -I -S /hook.py"}]}]}


def _args(port: int = 8811) -> list[str]:
    return boxed.claude_args("/abs/claude", "approver", port, HOOKS, "first words")


def _after(args: list[str], flag: str) -> str:
    return args[args.index(flag) + 1]


def test_the_approver_has_no_tools_of_its_own():
    args = _args()
    assert _after(args, "--tools") == ""
    assert args[0] == "/abs/claude"


def test_the_approver_loads_none_of_the_owners_settings_files():
    assert _after(_args(), "--setting-sources") == ""


def test_the_approver_reaches_only_the_bridge():
    args = _args(9123)
    assert "--strict-mcp-config" in args
    servers = json.loads(_after(args, "--mcp-config"))["mcpServers"]
    assert list(servers) == ["claude-voice"]
    bridge = servers["claude-voice"]
    assert bridge["type"] == "http" and bridge["url"] == "http://127.0.0.1:9123/mcp"
    assert bridge["headers"] == {"Authorization": "Bearer ${CLAUDE_VOICE_APPROVER_TOKEN}"}


def test_the_approver_runs_on_a_pinned_model():
    assert _after(_args(), "--model") == boxed.MODEL


def test_the_approver_may_call_only_its_two_tools():
    settings = json.loads(_after(_args(), "--settings"))
    assert settings["permissions"]["allow"] == [
        "mcp__claude-voice__approver_boxes",
        "mcp__claude-voice__approver_answer",
    ]
    assert "defaultMode" not in settings["permissions"]


def test_the_approvers_only_hook_is_the_one_the_bridge_gives():
    settings = json.loads(_after(_args(), "--settings"))
    assert settings["hooks"] == HOOKS
    assert "disableAllHooks" not in settings
    assert set(settings) == {"hooks", "permissions"}


def test_the_first_words_come_last_after_a_separator():
    args = _args()
    assert args[-2:] == ["--", "first words"]
