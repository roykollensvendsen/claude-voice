"""The approver starts boxed in: only the bridge's two approver tools, no tools of its own,
none of the owner's settings files, and only the bridge's own hook (approver-v1 v1.7)."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
from mcp.server.auth.provider import AccessToken
from mcp.shared.exceptions import MCPError
from mcp.types import ListToolsResult, Tool

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


# -- the token opens only the two approver tools ----------------------------------------------


def _ctx(method: str, tool: str = "", client: str | None = boxed.CLIENT) -> SimpleNamespace:
    scope = {}
    if client is not None:
        scope["user"] = AuthenticatedUser(AccessToken(token="t", client_id=client, scopes=["claude"]))
    return SimpleNamespace(method=method, params={"name": tool}, request=SimpleNamespace(scope=scope))


def _tools(*names: str) -> ListToolsResult:
    return ListToolsResult(tools=[Tool(name=n, inputSchema={"type": "object"}) for n in names])


async def _reached(ctx: SimpleNamespace, answer: object = "done") -> tuple[bool, object]:
    seen = []

    async def call_next(c):
        seen.append(c)
        return answer

    out = await boxed.gate(ctx, call_next)
    return bool(seen), out


@pytest.mark.parametrize("tool", ["send_task", "start_role", "approve", "garage_request"])
async def test_the_approvers_token_opens_only_its_two_tools(tool):
    reached, out = await _reached(_ctx("tools/call", tool))
    assert not reached and out.is_error
    for allowed in boxed.TOOLS:
        assert (await _reached(_ctx("tools/call", allowed)))[0]


async def test_the_approver_sees_only_its_two_tools():
    _, out = await _reached(_ctx("tools/list"), _tools("send_task", "approver_boxes", "approver_answer", "approve"))
    assert [t.name for t in out.tools] == ["approver_boxes", "approver_answer"]


async def test_the_approver_sees_only_its_two_tools_as_the_bridge_really_answers():
    """The server hands the middleware the list already as JSON, not as a model (seen live)."""
    listed = _tools("send_task", "approver_boxes", "approver_answer").model_dump(mode="json", by_alias=True)
    _, out = await _reached(_ctx("tools/list"), listed)
    assert [t["name"] for t in out["tools"]] == ["approver_boxes", "approver_answer"]


async def test_another_login_is_not_narrowed():
    for client in ("static-token", "a-phone", None):
        reached, out = await _reached(_ctx("tools/call", "send_task", client))
        assert reached and out == "done"
    _, out = await _reached(_ctx("tools/list", client="static-token"), _tools("send_task", "approve"))
    assert [t.name for t in out.tools] == ["send_task", "approve"]


@pytest.mark.parametrize("method", ["resources/list", "resources/read", "prompts/get", "completion/complete"])
async def test_the_approvers_token_asks_nothing_but_tools(method):
    with pytest.raises(MCPError):
        await _reached(_ctx(method))
    for open_ in ("initialize", "ping", "notifications/initialized"):
        assert (await _reached(_ctx(open_)))[0]
