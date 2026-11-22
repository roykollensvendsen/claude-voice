"""A plugin's approval covers the branch its checkout follows (plugin-config-commands-v1, f4
and f5): a later change of branch stops it, as a move of its folder does. Approvals from before
are given the branch once, from the settings as they stand, so nothing stops at the upgrade."""

import hashlib
import json

from mcp.client import Client
from test_plugins import GARAGE as REAL_GARAGE
from test_plugins import bridge, stand_in

from claude_voice import config, plugins

VACUUM = config.PluginSpec(
    name="vacuum", command="dreame-mcp", args=[], cwd="/home/x/dreame-mcp", tools=["vacuum_status"], branch="voice"
)
GARAGE = config.PluginSpec(
    name="garage", command="garage-door-mcp", args=["--simulated"], cwd="/home/x/garage-door", tools=["garage_status"]
)
TV = config.PluginSpec(name="tv", command="tv-mcp", args=[], cwd="/home/x/tv-mcp", tools=["tv_status"], branch="next")
LISTING = {"vacuum_status": {"schema": {}}, "garage_status": {"schema": {}}, "tv_status": {"schema": {}}}


def _as_before(spec):
    """The two fingerprints exactly as the bridge wrote them before the branch counted."""

    def hashed(shape):
        return hashlib.sha256(json.dumps(shape, sort_keys=True).encode()).hexdigest()

    place = {"command": spec.command, "args": spec.args, "cwd": spec.cwd}
    return hashed({**place, "tools": sorted(spec.tools)}), hashed(place)


def _approved_before(path, *specs):
    """An approvals file as an older bridge left it, with no branch in it."""
    body = {}
    for spec in specs:
        record = plugins._record(spec, LISTING)
        record["runs"], record["place"] = _as_before(spec)
        body[spec.name] = record
    path.write_text(json.dumps(body))


def _moved(spec, **changed):
    return config.PluginSpec(**{**spec.__dict__, **changed})


def test_a_plugin_on_main_keeps_the_fingerprints_it_had():
    """The branch is left out of the hash when it is main, so no approval on main changes."""
    assert (plugins.fingerprint(GARAGE), plugins.place_fingerprint(GARAGE)) == _as_before(GARAGE)


def test_a_change_of_branch_stops_the_plugin(tmp_path):
    approvals = tmp_path / "approved.json"
    plugins.approve(VACUUM, LISTING, approvals)
    assert plugins.approved(VACUUM, approvals)
    assert not plugins.approved(_moved(VACUUM, branch="mine"), approvals)
    assert not plugins.known_place(_moved(VACUUM, branch="mine"), approvals)
    assert not plugins.approved(_moved(GARAGE, branch="mine"), tmp_path / "none.json")


def test_an_approval_from_before_is_given_its_branch_once_and_keeps_running(tmp_path):
    approvals = tmp_path / "approved.json"
    _approved_before(approvals, VACUUM)
    assert not plugins.approved(VACUUM, approvals), "before the upgrade's own step, the branch is missing"
    plugins.migrate_branches([VACUUM], approvals)
    assert plugins.approved(VACUUM, approvals)
    record = plugins.read_approvals(approvals)["vacuum"]
    assert record["migrated_branch"]["value"] == "voice"
    assert record["migrated_branch"]["at"]
    assert not plugins.approved(_moved(VACUUM, branch="mine"), approvals), "a later change still stops it"


def test_the_branch_is_given_once_and_a_second_start_changes_nothing(tmp_path):
    approvals = tmp_path / "approved.json"
    _approved_before(approvals, VACUUM, GARAGE)
    plugins.migrate_branches([VACUUM, GARAGE], approvals)
    after_first = approvals.read_text()
    later = [_moved(VACUUM, branch="mine"), _moved(GARAGE, branch="mine")]
    assert plugins.migrate_branches(later, approvals) == []
    assert approvals.read_text() == after_first, "a later branch change is never written in"
    assert not any(plugins.approved(spec, approvals) for spec in later)


def test_an_approval_that_did_not_match_is_not_given_a_branch(tmp_path):
    approvals = tmp_path / "approved.json"
    _approved_before(approvals, VACUUM)
    moved = _moved(VACUUM, command="something-else")
    assert plugins.migrate_branches([moved], approvals) == []
    assert "migrated_branch" not in plugins.read_approvals(approvals)["vacuum"]
    assert not plugins.approved(moved, approvals)


def test_every_plugin_frozen_on_a_branch_other_than_main_is_named(tmp_path):
    approvals = tmp_path / "approved.json"
    _approved_before(approvals, VACUUM, GARAGE, TV)
    frozen = plugins.migrate_branches([VACUUM, GARAGE, TV], approvals)
    assert frozen == [("tv", "next"), ("vacuum", "voice")]
    assert plugins.approved(GARAGE, approvals)
    said = plugins.frozen_branches_said(frozen)
    assert "tv: next" in said
    assert "vacuum: voice" in said
    assert "garage" not in said
    assert "take back" in said
    assert plugins.frozen_branches_said([]) is None


async def test_the_bridge_gives_the_branch_at_its_start_and_tells_the_owner(tmp_path):
    """An approval from before, for a plugin on its own branch, keeps working through the
    upgrade, and the owner hears which branch was frozen."""
    on_voice = _moved(REAL_GARAGE, branch="voice")
    fake, _ = stand_in()
    listing = plugins.Plugin(on_voice, lambda params: Client(fake)).listing()
    record = plugins._record(on_voice, listing)
    record["runs"], record["place"] = _as_before(on_voice)
    (tmp_path / "approved.json").write_text(json.dumps({"garage": record}))
    server = bridge(tmp_path, [on_voice], approve=False, factory=lambda params: Client(fake))
    async with Client(server) as c:
        tools = {t.name for t in (await c.list_tools()).tools}
        # A voice that polled before the restart passes its old cursor; b0.f0 is the oldest.
        news = (await c.call_tool("whats_new", {"cursor": "b0.f0"})).structured_content
    assert "garage_status" in tools
    said = [e["text"] for e in news["events"] if e["kind"] == "plugin_branch"]
    assert len(said) == 1
    assert "garage: voice" in said[0]
