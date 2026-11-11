"""A plugin already approved is restarted, and approved again by voice in developer mode.

The decision is ADR-CV-010: the same tools taking the same inputs run without asking and
are logged; a changed tool is approved by the owner's voice, only in a developer mode the
owner switched on by voice; a new plugin, command or folder is still typed.
"""

import asyncio
import json
import logging

from mcp.client import Client
from mcp.server.mcpserver import MCPServer
from test_plugins import GARAGE, Unready, bridge, stand_in, tool_names

from claude_voice import devmode, plugin_desk, plugins
from claude_voice.config import PluginSpec

LEVEL = {
    "branch": "main",
    "commit": {"hash": "abc1234", "subject": "feat: something"},
    "dirty": False,
    "tracking": {"name": "origin/main", "ahead": 0, "behind": 0},
}


class Clock:
    def __init__(self, now=1_000_000.0):
        self.now = now

    def __call__(self):
        return self.now


def desk(tmp_path, fake, git=None, mode_on=True, clock=None):
    """A desk over a fresh MCP server, the garage approved against `fake`'s first listing."""
    clock = clock or Clock()
    approvals = tmp_path / "approved.json"
    mode = devmode.DeveloperMode(tmp_path / "developer-mode.json", clock=clock)
    if mode_on:
        mode.switch_on()
    server = MCPServer("bridge")
    holder = {"fake": fake}
    factory = lambda params: Client(holder["fake"])  # noqa: E731
    plugins.approve(GARAGE, plugins.Plugin(GARAGE, factory).listing(), approvals)
    active = plugins.start_approved(server, [GARAGE], approvals, factory)
    made = plugin_desk.Desk(
        server,
        active,
        specs=lambda: [GARAGE],
        approvals=approvals,
        client=factory,
        git=lambda folder: dict(git or LEVEL),
        mode=mode,
        questions=devmode.Questions(clock=clock),
    )
    return made, holder, server, approvals


# -- the same tools: run, logged, restarted one at a time --------------------------------


def test_a_known_plugin_accepted_again_is_logged_with_its_commit(tmp_path, caplog):
    fake, _ = stand_in()
    approvals = tmp_path / "approved.json"
    factory = lambda params: Client(fake)  # noqa: E731
    plugins.approve(GARAGE, plugins.Plugin(GARAGE, factory).listing(), approvals)
    with caplog.at_level(logging.INFO, logger="claude_voice"):
        plugins.start_approved(MCPServer("bridge"), [GARAGE], approvals, factory, commit_of=lambda folder: "abc1234")
    said = [r.getMessage() for r in caplog.records if r.name == "claude_voice"]
    assert "plugin garage accepted again unchanged at abc1234" in said


async def test_plugin_restart_starts_one_plugin_again_and_keeps_its_tools(tmp_path):
    fake, _ = stand_in()
    made, _, server, _ = desk(tmp_path, fake)
    before = made.active[0]
    out = await made.restart("garage")
    assert out["ok"] and "garage" in out["say"]
    assert made.active[0] is not before and before.state != "running"
    assert {"garage_status", "garage_request"} <= await tool_names(server)
    async with Client(server) as c:
        status = (await c.call_tool("garage_status", {})).structured_content
    assert status == {"door": "closed"}


def unhurried():
    """A stand-in garage whose status takes a moment, as a real device does."""
    server = MCPServer("garage")

    @server.tool()
    async def garage_status() -> dict:
        """Where the door is."""
        await asyncio.sleep(0.5)
        return {"door": "closed"}

    @server.tool()
    async def garage_request(action: str) -> dict:
        """Ask to open or close the door."""
        return {"token": "t1", "action": action}

    return server


async def test_a_restart_lets_a_call_already_running_finish_first(tmp_path):
    made, _, server, _ = desk(tmp_path, unhurried())
    async with Client(server) as c:
        running = asyncio.create_task(c.call_tool("garage_status", {}))
        await asyncio.sleep(0.2)
        out = await made.restart("garage")
        answer = await running
    assert out["ok"]
    assert not answer.is_error
    assert answer.structured_content == {"door": "closed"}


async def test_a_restarted_plugin_whose_inputs_changed_is_not_offered(tmp_path):
    before, _ = stand_in()
    after, _ = stand_in(phrase=True)
    made, holder, server, _ = desk(tmp_path, before)
    holder["fake"] = after
    out = await made.restart("garage")
    assert not out["ok"] and "approve" in out["say"]
    assert not any(n.startswith("garage") for n in await tool_names(server))


async def test_a_restart_that_finds_the_plugin_not_started_says_try_again_and_asks_no_approval(tmp_path):
    fake, _ = stand_in()
    made, *_ = desk(tmp_path, fake)
    made.client = Unready
    out = await made.restart("garage")
    assert not out["ok"] and "garage did not start in time; try again" in out["say"]
    assert "approve" not in out["say"]


async def test_a_restart_that_fails_keeps_the_plugin_that_was_running(tmp_path):
    fake, _ = stand_in()
    made, _, server, _ = desk(tmp_path, fake)
    before = made.active[0]
    async with Client(server) as c:
        await c.call_tool("garage_status", {})  # the plugin is running
        made.client = Unready  # its new start does not answer in time
        out = await made.restart("garage")
        status = await c.call_tool("garage_status", {})
    assert not out["ok"]
    assert made.active == [before] and before.state == "running"
    assert {"garage_status", "garage_request"} <= await tool_names(server)
    assert not status.is_error and status.structured_content == {"door": "closed"}


async def test_restarting_a_plugin_not_in_the_settings_file_is_refused(tmp_path):
    fake, _ = stand_in()
    made, *_ = desk(tmp_path, fake)
    out = await made.restart("toaster")
    assert not out["ok"] and "toaster" in out["say"]


# -- developer mode ----------------------------------------------------------------------


def test_developer_mode_ends_by_itself_after_eight_hours(tmp_path, caplog):
    clock = Clock()
    mode = devmode.DeveloperMode(tmp_path / "dev.json", clock=clock)
    assert mode.state()["on"] is False
    with caplog.at_level(logging.INFO, logger="claude_voice"):
        on = mode.switch_on()
        clock.now += 8 * 3600 - 1
        still = mode.state()
        clock.now += 2
        ended = mode.state()
    assert on["on"] and on["until"] and still["on"] and not ended["on"]
    said = [r.getMessage() for r in caplog.records if r.name == "claude_voice"]
    assert any("developer mode on" in s for s in said) and any("ended by itself" in s for s in said)


def test_developer_mode_is_kept_across_a_restart_of_the_bridge(tmp_path):
    clock = Clock()
    devmode.DeveloperMode(tmp_path / "dev.json", clock=clock).switch_on()
    again = devmode.DeveloperMode(tmp_path / "dev.json", clock=clock)
    assert again.state()["on"]
    assert json.loads((tmp_path / "dev.json").read_text())["until"] == clock.now + 8 * 3600


def test_a_question_token_works_once_and_only_for_two_minutes():
    clock = Clock()
    asked = devmode.Questions(clock=clock)
    first = asked.ask("developer_mode", {})
    assert asked.take("plugin", first) is None  # a token answers only its own kind of question
    assert asked.take("developer_mode", first) == {}
    assert asked.take("developer_mode", first) is None
    late = asked.ask("developer_mode", {})
    clock.now += 121
    assert asked.take("developer_mode", late) is None


def test_only_the_owners_voice_in_the_claude_app_can_approve_a_plugin_or_switch_developer_mode_on(tmp_path):
    fake, _ = stand_in()
    made, *_ = desk(tmp_path, fake, mode_on=False)
    for caller in ("local", "in_process", "voice_app"):
        out = made.mode_request(caller)
        assert not out["ok"] and "Claude app" in out["say"]
    asked = made.mode_request("claude_app")
    assert asked["ok"] and "token" in asked and "eight hours" in asked["say"]
    assert not made.mode_confirm("local", asked["token"])["ok"]
    assert made.mode_confirm("claude_app", asked["token"])["ok"]
    assert made.mode.state()["on"]


def test_developer_mode_is_switched_off_at_once_from_anywhere(tmp_path):
    fake, _ = stand_in()
    made, *_ = desk(tmp_path, fake)
    out = made.mode_off("local")
    assert out["ok"] and not made.mode.state()["on"]


# -- a changed plugin, approved by voice -------------------------------------------------


async def changed_desk(tmp_path, git=None, mode_on=True):
    before, _ = stand_in()
    after, asked = stand_in(phrase=True)
    made, holder, server, approvals = desk(tmp_path, before, git=git, mode_on=mode_on)
    holder["fake"] = after
    await made.restart("garage")  # the new code no longer matches, so its tools are withdrawn
    return made, server, approvals


async def test_a_changed_plugin_is_approved_by_voice_and_offered_at_once(tmp_path):
    made, server, approvals = await changed_desk(tmp_path)
    asked = await made.approve_request("claude_app", "garage")
    assert asked["ok"] and "garage_request" in asked["say"] and "phrase?" in asked["say"]
    assert "garage_status" not in asked["say"]  # an unchanged tool is not read out
    done = await made.approve_confirm("claude_app", asked["token"])
    assert done["ok"]
    assert "garage_request" in await tool_names(server)
    again = await made.approve_confirm("claude_app", asked["token"])
    assert not again["ok"]  # the token is spent


async def test_a_changed_plugin_is_approved_by_voice_only_in_developer_mode(tmp_path):
    made, *_ = await changed_desk(tmp_path, mode_on=False)
    out = await made.approve_request("claude_app", "garage")
    assert not out["ok"] and "developer mode" in out["say"].lower()


async def test_a_question_asked_in_developer_mode_is_not_confirmed_once_it_is_off(tmp_path):
    made, *_ = await changed_desk(tmp_path)
    asked = await made.approve_request("claude_app", "garage")
    made.mode_off("local")
    assert not (await made.approve_confirm("claude_app", asked["token"]))["ok"]


async def test_only_a_clean_checkout_level_with_its_followed_branch_is_approved_by_voice(tmp_path):
    for git in (
        {**LEVEL, "dirty": True},
        {**LEVEL, "branch": "feat/x", "tracking": {"name": "origin/feat/x", "ahead": 0, "behind": 0}},
        {**LEVEL, "tracking": {"name": "origin/main", "ahead": 1, "behind": 0}},
        {**LEVEL, "tracking": None},
    ):
        made, *_ = await changed_desk(tmp_path / str(id(git)), git=git)
        out = await made.approve_request("claude_app", "garage")
        assert not out["ok"], git


async def test_code_that_moved_after_the_question_is_not_approved(tmp_path):
    made, *_ = await changed_desk(tmp_path)
    asked = await made.approve_request("claude_app", "garage")
    made.git = lambda folder: {**LEVEL, "commit": {"hash": "def5678", "subject": "later"}}
    out = await made.approve_confirm("claude_app", asked["token"])
    assert not out["ok"] and "changed" in out["say"]


async def test_a_new_command_or_folder_is_never_approved_by_voice(tmp_path):
    made, *_ = await changed_desk(tmp_path)
    moved = PluginSpec(
        name="garage", command="other-mcp", args=[], cwd=GARAGE.cwd, tools=GARAGE.tools, timeout=GARAGE.timeout
    )
    made.specs = lambda: [moved]
    out = await made.approve_request("claude_app", "garage")
    assert not out["ok"] and "claude-voice plugins approve garage" in out["say"]
    never = PluginSpec(name="toaster", command="toaster-mcp", args=[], cwd="/x", tools=["toast"])
    made.specs = lambda: [never]
    out = await made.approve_request("claude_app", "toaster")
    assert not out["ok"] and "claude-voice plugins approve toaster" in out["say"]


def old_format(approvals):
    """Cut every approval in `approvals` back to what the bridge wrote before developer mode:
    the plugin's start and its tools' inputs, nothing else."""
    body = json.loads(approvals.read_text())
    approvals.write_text(json.dumps({n: {"runs": a["runs"], "inputs": a["inputs"]} for n, a in body.items()}))


async def test_an_approval_from_before_developer_mode_is_upgraded_once_found_unchanged(tmp_path, caplog):
    fake, _ = stand_in()
    made, holder, server, approvals = desk(tmp_path, fake)
    old_format(approvals)
    assert set(json.loads(approvals.read_text())["garage"]) == {"runs", "inputs"}
    with caplog.at_level(logging.INFO, logger="claude_voice"):
        out = await made.restart("garage")
    assert out["ok"]
    entry = json.loads(approvals.read_text())["garage"]
    assert {"place", "tools", "marks"} <= set(entry)
    assert "plugin garage approval upgraded" in [r.getMessage() for r in caplog.records]
    assert await made.pick_up() == []  # writing the upgrade does not restart the plugin again
    # The owner adds a tool in the settings file: once upgraded, the plugin is known, so the
    # change is approved by voice rather than typed.
    wider = PluginSpec(
        name="garage",
        command=GARAGE.command,
        args=GARAGE.args,
        cwd=GARAGE.cwd,
        tools=[*GARAGE.tools, "garage_arm"],
        timeout=GARAGE.timeout,
    )
    made.specs = lambda: [wider]
    asked = await made.approve_request("claude_app", "garage")
    assert asked["ok"] and "garage_arm" in asked["say"]


async def test_an_approval_from_before_developer_mode_is_upgraded_only_when_nothing_differs(tmp_path):
    before, _ = stand_in()
    made, holder, server, approvals = desk(tmp_path, before)
    old_format(approvals)
    holder["fake"], _ = stand_in(phrase=True)  # garage_request now takes one input more
    out = await made.restart("garage")
    assert not out["ok"]
    assert set(json.loads(approvals.read_text())["garage"]) == {"runs", "inputs"}
    changed = plugins.Plugin(GARAGE, lambda params: Client(holder["fake"])).listing()
    assert not plugins.upgrade(GARAGE, changed, approvals)  # asked directly, it still refuses
    assert set(json.loads(approvals.read_text())["garage"]) == {"runs", "inputs"}
    moved = PluginSpec(
        name="garage",
        command=GARAGE.command,
        args=GARAGE.args,
        cwd=GARAGE.cwd,
        tools=[*GARAGE.tools, "garage_arm"],
        timeout=GARAGE.timeout,
    )
    made.specs = lambda: [moved]
    out = await made.approve_request("claude_app", "garage")
    assert not out["ok"] and "claude-voice plugins approve garage" in out["say"]


async def test_an_unchanged_plugin_needs_no_approval(tmp_path):
    fake, _ = stand_in()
    made, *_ = desk(tmp_path, fake)
    out = await made.approve_request("claude_app", "garage")
    assert not out["ok"] and "plugin_restart" in out["say"]


async def test_a_changed_read_only_mark_needs_approving_again(tmp_path):
    fake, _ = stand_in()
    factory = lambda params: Client(fake)  # noqa: E731
    approvals = tmp_path / "approved.json"
    listing = plugins.Plugin(GARAGE, factory).listing()
    plugins.approve(GARAGE, listing, approvals)
    marked = {tool: {**found, "read_only": True} for tool, found in listing.items()}
    assert plugins.inputs_approved(GARAGE, listing, approvals)
    assert not plugins.inputs_approved(GARAGE, marked, approvals)


# -- through the bridge ------------------------------------------------------------------


async def test_the_health_report_and_the_work_page_say_whether_developer_mode_is_on(tmp_path, monkeypatch):
    monkeypatch.setattr(devmode, "STATE", tmp_path / "developer-mode.json")
    fake, _ = stand_in()
    server = bridge(tmp_path, [GARAGE], factory=lambda params: Client(fake))
    async with Client(server) as c:
        health = (await c.call_tool("health", {})).structured_content
        names = {t.name for t in (await c.list_tools()).tools}
    assert health["developer_mode"] == {"on": False, "until": None}
    assert {
        "plugin_restart",
        "developer_mode_request",
        "developer_mode_confirm",
        "developer_mode_off",
        "plugin_approve_request",
        "plugin_approve_confirm",
    } <= names


def changing_bridge(tmp_path):
    """The bridge with the garage approved as it was, and a switch to give it new code."""
    before, _ = stand_in()
    holder = {"fake": before}
    server = bridge(tmp_path, [GARAGE], factory=lambda params: Client(holder["fake"]))
    return server, holder


async def test_a_typed_approval_takes_effect_in_the_running_bridge(tmp_path):
    """The owner types `claude-voice plugins approve garage` while the bridge runs."""
    server, holder = changing_bridge(tmp_path)
    holder["fake"], _ = stand_in(phrase=True)
    await server.plugin_desk.restart("garage")  # the new code takes a new input: withdrawn
    assert "garage_request" not in await tool_names(server)
    listing = plugins.Plugin(GARAGE, lambda params: Client(holder["fake"])).listing()
    plugins.approve(GARAGE, listing, tmp_path / "approved.json")  # what the typed command writes
    assert await server.plugin_desk.pick_up() == ["garage"]
    assert "garage_request" in await tool_names(server)
    assert await server.plugin_desk.pick_up() == []  # once picked up, nothing more to do


async def test_connected_clients_hear_when_the_tools_change(tmp_path):
    server, holder = changing_bridge(tmp_path)
    heard = []

    async def listen(message):
        heard.append(repr(message))

    async with Client(server, mode="legacy", message_handler=listen) as c:
        assert c.server_capabilities.tools.list_changed is True
        await c.call_tool("health", {})  # the bridge knows a client once it has called
        holder["fake"], _ = stand_in(phrase=True)
        await c.call_tool("plugin_restart", {"name": "garage"})
        names = {t.name for t in (await c.list_tools()).tools}
    assert any("ToolListChanged" in h for h in heard)
    assert "garage_request" not in names


async def test_a_call_from_this_computer_cannot_switch_developer_mode_on_through_the_bridge(tmp_path, monkeypatch):
    monkeypatch.setattr(devmode, "STATE", tmp_path / "developer-mode.json")
    fake, _ = stand_in()
    server = bridge(tmp_path, [GARAGE], factory=lambda params: Client(fake))
    async with Client(server) as c:
        out = (await c.call_tool("developer_mode_request", {})).structured_content
    assert not out["ok"] and "token" not in out


async def test_a_question_read_aloud_states_how_things_are_and_then_asks(tmp_path):
    # Read aloud, "Switch it on?" sounds like something already happening. Every question
    # says how things are now, then asks "Do you want me to ...", like the garage door's.
    fake, _ = stand_in()
    made, *_ = desk(tmp_path, fake, mode_on=False)
    switching = made.mode_request("claude_app")["say"]
    made, *_ = await changed_desk(tmp_path / "changed")
    approving = (await made.approve_request("claude_app", "garage"))["say"]
    for say in (switching, approving):
        first, _, rest = say.partition(". ")
        assert "Do you want me to" not in first, say  # the state comes first
        assert "Do you want me to" in rest and "?" in rest, say
    assert switching.startswith("Developer mode is off.")
