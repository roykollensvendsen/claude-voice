"""A role the bridge starts is told its organisation's rules, and cannot change them.

The bridge starts a role's session with `--settings`. For every role that is not a
manager, those settings now add the start hooks that read the rule packs to it (org-rules
install plan B2). Every role, managers included, is refused writes to the packs and their
records. The fingerprints come from the packs' pins file, which only the owner's install
writes; before the first install there are no pins, so no hook, and no lane is stopped.
"""

from __future__ import annotations

import io
import json
import shlex
import sys
from pathlib import Path

import pytest
from test_permissions import decision

from claude_voice import org_rules, permissions, roles

HOME = Path("/home/someone")
PINS = {"_shared": "a" * 12, "voice": "b" * 12}


@pytest.fixture
def pins(tmp_path: Path) -> Path:
    path = tmp_path / "pins.json"
    path.write_text(json.dumps(PINS))
    return path


def settings(role: dict, pins: Path) -> dict:
    text = roles.role_settings(role, pins_file=pins, home=HOME)
    return json.loads(text) if text else {}


def hooks(found: dict, event: str) -> list[str]:
    return [h["command"] for m in found.get("hooks", {}).get(event, []) for h in m["hooks"] if m["matcher"] == "*"]


def denied(found: dict) -> permissions.Rules:
    return permissions.Rules(allow=[], deny=found.get("permissions", {}).get("deny", []))


@pytest.mark.parametrize("event", ["SessionStart", "SubagentStart"])
def test_a_lane_role_is_told_its_rules_at_every_start(pins, event):
    (command,) = hooks(settings({"name": "builder"}, pins), event)
    words = shlex.split(command)
    assert words[:4] == [sys.executable, "-I", "-m", "claude_voice.org_rules"]  # org_rules.COMMAND
    assert words[4:] == ["emit", "voice", "--event", event, "--shared-sha", "a" * 12, "--sha", "b" * 12]


def test_the_hook_command_is_one_the_emit_command_takes(pins, tmp_path):
    (command,) = hooks(settings({"name": "builder"}, pins), "SessionStart")
    out = io.StringIO()
    argv = shlex.split(command)[len(org_rules.COMMAND) :]
    assert org_rules.main(argv, root=tmp_path, stdin=io.StringIO("{}"), stdout=out) == 0
    told = json.loads(out.getvalue())["hookSpecificOutput"]
    assert told["hookEventName"] == "SessionStart"
    assert "missing" in told["additionalContext"]  # these test packs are not installed


def test_a_manager_is_not_told_the_lanes_rules(pins):
    found = settings({"name": "manager", "manager": True}, pins)
    assert hooks(found, "SessionStart") == [] and hooks(found, "SubagentStart") == []


@pytest.mark.parametrize("pins_text", [None, "{}", '{"_shared": "aaaaaaaaaaaa"}', "not json", '["a"]'])
def test_before_the_packs_are_installed_a_lane_gets_no_hook(tmp_path, pins_text):
    path = tmp_path / "pins.json"
    if pins_text is not None:
        path.write_text(pins_text)
    assert hooks(settings({"name": "builder"}, path), "SessionStart") == []


@pytest.mark.skipif(sys.platform == "win32", reason="the deny paths are POSIX home paths")
@pytest.mark.parametrize("manager", [False, True])
@pytest.mark.parametrize(
    "path",
    [
        "~/.config/claude-voice/orgs/voice/rules.md",
        "~/.config/claude-voice/orgs/pins.json",
        "~/.local/state/claude-voice/orgs/voice/rules.json",
    ],
)
def test_no_role_may_write_the_packs_or_their_records(tmp_path, manager, path):
    found = settings({"name": "r", "manager": manager}, tmp_path / "none.json")
    rules = found.get("permissions", {}).get("deny", [])
    full = path.replace("~", str(HOME))
    folder = path.split("/orgs/")[0] + "/orgs/**"
    assert f"Edit({folder})" in rules and f"Write({folder})" in rules
    for command in (f"tee {path}", f"tee {full}", f"cp x {full}", f"mv x {path}", f"sed -i s/a/b/ {full}"):
        assert decision(denied(found), command) == "refused", command
    assert decision(denied(found), f"cat {full}") != "refused"


def test_the_push_guard_stays_for_a_role_that_asks_for_it(pins):
    found = settings({"name": "contribute", "guard": True, "upstream": "owner/repo"}, pins)
    (guard,) = [h["command"] for m in found["hooks"]["PreToolUse"] for h in m["hooks"]]
    assert "claude_voice.guard --upstream owner/repo" in guard
    assert hooks(found, "SessionStart")
