"""A plugin keeps running while a new or changed tool waits for the owner (plugin-config-commands-v1,
2.2, b1 and f6): only the tools approved as they are now are offered, and the rest wait."""

from mcp.client import Client
from test_plugins import GARAGE, bridge, stand_in

from claude_voice import config, plugins, work


def _with(**changed):
    return config.PluginSpec(**{**GARAGE.__dict__, **changed})


def _approved(tmp_path, spec=GARAGE, **server):
    """The owner's approval of `spec` as the stand-in describes it; the approvals file."""
    approvals = tmp_path / "approved.json"
    fake, _ = stand_in(**server)
    plugins.approve(spec, plugins.Plugin(spec, lambda params: Client(fake)).listing(), approvals)
    return approvals


def _checked(spec, approvals, **server):
    fake, _ = stand_in(**server)
    return plugins.check_one(spec, approvals, lambda params: Client(fake))


def test_a_tool_added_in_the_settings_waits_while_the_others_run(tmp_path):
    approvals = _approved(tmp_path)
    plugin, listing, why = _checked(_with(tools=[*GARAGE.tools, "garage_arm"]), approvals)
    assert plugin is not None
    assert plugin.spec.tools == ["garage_status", "garage_request"]
    assert sorted(listing) == ["garage_request", "garage_status"]
    assert list(plugin.waiting) == ["garage_arm"]
    assert why == "running, with 1 waiting: garage_arm"
    assert plugin.status()["waiting"] == ["garage_arm"]


def test_a_tool_removed_from_the_settings_only_narrows(tmp_path):
    approvals = _approved(tmp_path)
    plugin, _, why = _checked(_with(tools=["garage_status"]), approvals)
    assert plugin is not None and plugin.spec.tools == ["garage_status"] and not plugin.waiting
    assert why == "started"


def test_a_move_together_with_a_tools_change_still_stops_the_plugin(tmp_path):
    approvals = _approved(tmp_path)
    more = [*GARAGE.tools, "garage_arm"]
    for moved in (_with(tools=more, command="other"), _with(tools=more, branch="mine"), _with(cwd="/elsewhere")):
        assert _checked(moved, approvals)[2] == "not approved"


def test_a_tool_with_changed_inputs_waits_while_the_others_run(tmp_path):
    approvals = _approved(tmp_path)
    plugin, _, why = _checked(GARAGE, approvals, phrase=True)
    assert plugin is not None and plugin.spec.tools == ["garage_status"]
    assert list(plugin.waiting) == ["garage_request"]
    assert why == "running, with 1 waiting: garage_request"


def test_a_change_in_the_plugins_rules_makes_every_tool_wait(tmp_path):
    approvals = _approved(tmp_path)
    plugin, _, why = _checked(GARAGE, approvals, rules="New rules for the voice.")
    assert plugin is None and why == "tools changed"


def test_an_old_approval_without_each_tool_stops_the_plugin_on_any_change(tmp_path):
    approvals = _approved(tmp_path)
    body = plugins.read_approvals(approvals)
    del body["garage"]["tools"]
    plugins._save(approvals, body)
    assert _checked(GARAGE, approvals)[2] == "started"
    assert _checked(GARAGE, approvals, phrase=True)[2] == "tools changed"
    assert _checked(_with(tools=[*GARAGE.tools, "garage_arm"]), approvals)[2] == "not approved"


def test_a_next_naming_a_waiting_tool_is_refused(tmp_path):
    approvals = _approved(tmp_path)
    plugin, listing, _ = _checked(_with(tools=[*GARAGE.tools, "garage_arm"]), approvals)
    answer = {"door": "closed", "next": {"tool": "garage_arm", "args": {}}}
    assert plugins.follow_up(answer, plugin, listing, 0) == {"door": "closed"}


def test_a_waiting_tool_is_announced_once_per_state(tmp_path):
    told = tmp_path / "told.json"
    approvals = _approved(tmp_path)
    wider = _with(tools=[*GARAGE.tools, "garage_arm"])
    plugin, *_ = _checked(wider, approvals)
    said = plugins.announce_waiting(plugin, told)
    assert said == "garage has 1 tool waiting for your approval: garage_arm"
    again, *_ = _checked(wider, approvals)
    assert plugins.announce_waiting(again, told) is None, "a restart does not repeat it"
    changed = plugins.Plugin(wider, None)
    changed.waiting = {"garage_arm": "another fingerprint"}
    assert plugins.announce_waiting(changed, told), "a tool that changes again is announced again"
    _approved(tmp_path, wider)
    approved, *_ = _checked(wider, approvals)
    assert plugins.announce_waiting(approved, told) is None
    plugin, *_ = _checked(_with(tools=[*GARAGE.tools, "garage_arm"]), _approved(tmp_path))
    assert plugins.announce_waiting(plugin, told), "approved and later waiting again is announced"


def test_a_waiting_tool_waits_on_the_owner_on_the_work_page(tmp_path):
    approvals = _approved(tmp_path)
    plugin, *_ = _checked(_with(tools=[*GARAGE.tools, "garage_arm"]), approvals)
    running, *_ = _checked(GARAGE, approvals)
    [item] = plugins.waiting_items([plugin, running])
    assert item["id"] == "plugin-waiting-garage"
    assert item["text"] == "garage has 1 tool waiting for your approval: garage_arm"
    assert item["command"].endswith("plugins approve garage")


def test_approving_offers_the_waiting_tool(tmp_path):
    wider = _with(tools=[*GARAGE.tools, "garage_arm"])
    approvals = _approved(tmp_path)
    _approved(tmp_path, wider)
    plugin, _, why = _checked(wider, approvals)
    assert plugin is not None and plugin.spec.tools == wider.tools and not plugin.waiting
    assert why == "started"


async def test_the_bridge_tells_the_owner_of_a_waiting_tool_once_across_restarts(tmp_path):
    wider = _with(tools=[*GARAGE.tools, "garage_arm"])
    _approved(tmp_path)
    fake, _ = stand_in()
    told = []
    for _ in range(2):  # the bridge started twice, as after a restart
        server = bridge(tmp_path, [wider], approve=False, factory=lambda params: Client(fake))
        async with Client(server) as c:
            tools = {t.name for t in (await c.list_tools()).tools}
            news = (await c.call_tool("whats_new", {"cursor": "b0.f0"})).structured_content
        told = [e["text"] for e in news["events"] if e["kind"] == "plugin_waiting"]
        assert "garage_status" in tools and "garage_arm" not in tools
    assert told == ["garage has 1 tool waiting for your approval: garage_arm"]


def test_the_work_page_lists_what_the_bridge_knows_waits():
    item = {"id": "plugin-waiting-garage", "text": "garage has 1 tool waiting", "command": "x"}
    board = work.Board({}, lambda args: "[]", bridge_items=lambda: [item])
    assert item in board.report()["waiting_on_owner"]
