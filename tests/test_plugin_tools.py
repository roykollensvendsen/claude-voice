"""`plugins tools <name> --from-manifest`: a plugin's tools set from its own manifest, never
from free text and never approved (plugin-config-commands-v1, 2.1 and b2)."""

import json
import tomllib

import pytest

from claude_voice import plugin_tools, plugins
from claude_voice.settings_edit import Refused

LEVEL = {
    "branch": "main",
    "commit": {"hash": "abc1234", "subject": "feat: something"},
    "dirty": False,
    "tracking": {"name": "origin/main", "ahead": 0, "behind": 0},
}


def level(folder):
    return dict(LEVEL)


def _manifest(folder, tools, name="garage", command="garage-door-mcp"):
    folder.mkdir(parents=True, exist_ok=True)
    listed = ", ".join(f'"{t}"' for t in tools)
    (folder / "claude-voice-plugin.toml").write_text(
        f'manifest = 1\nname = "{name}"\ncommand = "{command}"\nargs = ["--simulated"]\ntools = [{listed}]\n',
        encoding="utf-8",
    )


def _setup(tmp_path, configured, approved_manifest, manifest_now):
    """A home with the garage's checkout, its settings and an approval that stored the
    manifest's tools as they were then (None: an approval from before that was stored)."""
    home = tmp_path / "home"
    checkout = home / "garage-door"
    _manifest(checkout, manifest_now)
    config = home / ".config" / "claude-voice" / "config.toml"
    config.parent.mkdir(parents=True)
    listed = ", ".join(f'"{t}"' for t in configured)
    config.write_text(
        '# the owner\'s note\n[plugins.garage]\ncommand = "garage-door-mcp"\nargs = ["--simulated"]\n'
        f'cwd = "~/garage-door"\ntools = [{listed}]\n',
        encoding="utf-8",
    )
    approvals = home / ".config" / "claude-voice" / "plugins-approved.json"
    record = {"runs": "x", "place": "y"}
    if approved_manifest is not None:
        record["manifest_tools"] = approved_manifest
    approvals.write_text(json.dumps({"garage": record}), encoding="utf-8")
    return home, config, approvals


def _tools(config):
    return tomllib.loads(config.read_text(encoding="utf-8"))["plugins"]["garage"]["tools"]


def _run(home, config, approvals, git=level):
    return plugin_tools.from_manifest(config, approvals, "garage", home=home, git=git)


def test_a_tool_new_in_the_manifest_is_added_and_a_second_run_changes_nothing(tmp_path):
    home, config, approvals = _setup(tmp_path, ["garage_status"], ["garage_status"], ["garage_status", "garage_arm"])
    said = _run(home, config, approvals)
    assert _tools(config) == ["garage_status", "garage_arm"]
    assert "# the owner's note" in config.read_text(encoding="utf-8"), "the rest of the file is kept"
    [backup] = (home / ".local/state/claude-voice/backups/bridge-config").iterdir()
    assert "garage_arm" not in backup.read_text(encoding="utf-8")
    assert any("garage_arm" in line for line in said)
    assert any(str(backup) in line and "undo" in line for line in said)
    assert _run(home, config, approvals) == ["garage: the tools already match its manifest; nothing changed"]


def test_a_tool_the_owner_left_out_is_not_added_again(tmp_path):
    manifest = ["garage_status", "garage_arm"]
    home, config, approvals = _setup(tmp_path, ["garage_status"], manifest, manifest)
    _run(home, config, approvals)
    assert _tools(config) == ["garage_status"]


def test_a_tool_gone_from_the_manifest_is_removed(tmp_path):
    both = ["garage_status", "garage_request"]
    home, config, approvals = _setup(tmp_path, both, both, ["garage_status"])
    _run(home, config, approvals)
    assert _tools(config) == ["garage_status"]


def test_tools_are_never_set_before_a_first_approval_stored_the_manifest(tmp_path):
    home, config, approvals = _setup(tmp_path, ["garage_status"], None, ["garage_status", "garage_arm"])
    with pytest.raises(Refused, match="approve .* once first"):
        _run(home, config, approvals)
    assert _tools(config) == ["garage_status"]


def test_tools_come_only_from_a_clean_checkout_level_with_its_branch(tmp_path):
    home, config, approvals = _setup(tmp_path, ["garage_status"], ["garage_status"], ["garage_status", "garage_arm"])
    with pytest.raises(Refused, match="not committed"):
        _run(home, config, approvals, git=lambda folder: {**LEVEL, "dirty": True})
    assert _tools(config) == ["garage_status"]


@pytest.mark.parametrize(("name", "command"), [("tv", "garage-door-mcp"), ("garage", "other-mcp")])
def test_a_manifest_of_another_plugin_or_command_is_refused(tmp_path, name, command):
    home, config, approvals = _setup(tmp_path, ["garage_status"], ["garage_status"], ["garage_status"])
    _manifest(home / "garage-door", ["garage_status", "garage_arm"], name=name, command=command)
    with pytest.raises(Refused):
        _run(home, config, approvals)
    assert _tools(config) == ["garage_status"]


def test_setting_tools_from_the_manifest_never_approves(tmp_path):
    home, config, approvals = _setup(tmp_path, ["garage_status"], ["garage_status"], ["garage_status", "garage_arm"])
    before = approvals.read_text(encoding="utf-8")
    _run(home, config, approvals)
    assert approvals.read_text(encoding="utf-8") == before


def test_an_approval_stores_the_manifests_tools_as_they_were(tmp_path):
    home, config, approvals = _setup(tmp_path, ["garage_status"], None, ["garage_status", "garage_arm"])
    from claude_voice import config as settings

    spec = settings.load(config, env={"HOME": str(home)}).plugins[0]
    plugins.approve(spec, {}, approvals)
    assert plugins.read_approvals(approvals)["garage"]["manifest_tools"] == ["garage_status", "garage_arm"]


def test_the_command_takes_tools_only_from_the_manifest(tmp_path, capsys):
    from claude_voice.cli import main

    home, config, approvals = _setup(tmp_path, ["garage_status"], ["garage_status"], ["garage_status", "garage_arm"])
    env = {"HOME": str(home)}
    assert main(["plugins", "tools", "garage", "garage_arm"], env=env) == 1
    assert "--from-manifest" in capsys.readouterr().err
    # The checkout here is no git checkout, so the command refuses and says why.
    assert main(["plugins", "tools", "garage", "--from-manifest"], env=env) == 1
    assert "not a git checkout" in capsys.readouterr().err
    assert _tools(config) == ["garage_status"]
