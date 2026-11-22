"""The bridge's own commands for its settings files (runs-as-owner v1.3 §6.1).

A session can no longer edit the bridge's settings or roles file directly. These commands
are the sanctioned path, and they keep what runs as the owner the owner's call: a key a
plugin runs with changes only to what the owner approved by typing. The replayed edits are
the manager's real ones from 06.11 to 17.11, with made-up values.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from claude_voice import plugins, settings_edit
from claude_voice.config import plugin_spec

DEMO = """\
[plugins.vacuum]
command = "uv"
args = ["run", "vacuum-mcp", "serve"]
cwd = "~/vacuum-mcp"
tools = ["vacuum_status", "vacuum_rooms"]
timeout = 30
branch = "main"

[plugins.mister]
command = "uv"
args = ["run", "mister-mcp", "serve", "--simulate"]
cwd = "~/mister-mcp"
tools = ["mister_status"]
"""


@pytest.fixture
def files(tmp_path):
    config = tmp_path / "config.toml"
    config.write_text(DEMO)
    approvals = tmp_path / "approved.json"
    return config, approvals


def _read(path: Path) -> dict:
    return tomllib.loads(path.read_text(encoding="utf-8"))


def _approve(config: Path, approvals: Path, name: str, home: Path) -> None:
    spec = plugin_spec(name, _read(config)["plugins"][name], str(home))
    plugins.approve(spec, {}, approvals)


def test_plugins_set_never_takes_a_typed_list_of_tools(files, tmp_path):
    """The tools come only from the plugin's manifest (plugin-config-commands-v1, 2.1)."""
    config, approvals = files
    before = _read(config)["plugins"]["vacuum"]["tools"]
    for typed in ('["vacuum_status", "vacuum_rooms", "vacuum_clean_rooms"]', '["vacuum_status"]'):
        with pytest.raises(settings_edit.Refused, match="plugins tools vacuum --from-manifest"):
            settings_edit.set_plugin(config, approvals, "vacuum", "tools", typed, home=tmp_path)
    assert _read(config)["plugins"]["vacuum"]["tools"] == before


def test_plugins_set_refuses_each_key_a_plugin_runs_with_unless_the_owner_approved_it(files, tmp_path):
    """Even an approved plugin: env, terminal_command and branch are no part of what the
    owner's approval covers, so no command sets them."""
    config, approvals = files
    _approve(config, approvals, "mister", tmp_path)
    for key, value in [
        ("command", '"sh"'),
        ("cwd", '"/tmp"'),
        ("args", '["run", "mister-mcp", "serve"]'),
        ("env", '{A = "b"}'),
        ("terminal_command", '"sh"'),
        ("branch", '"voice"'),
    ]:
        with pytest.raises(settings_edit.Refused):
            settings_edit.set_plugin(config, approvals, "mister", key, value, home=tmp_path)
    assert _read(config)["plugins"]["mister"]["args"][-1] == "--simulate"


def test_plugins_set_lets_the_args_back_to_what_the_owner_approved(files, tmp_path):
    config, approvals = files
    _approve(config, approvals, "mister", tmp_path)
    text = config.read_text().replace('"serve", "--simulate"]', '"serve"]')
    config.write_text(text)  # the args as they were changed by hand, before the deny
    settings_edit.set_plugin(
        config, approvals, "mister", "args", '["run", "mister-mcp", "serve", "--simulate"]', home=tmp_path
    )
    assert _read(config)["plugins"]["mister"]["args"][-1] == "--simulate"


def test_plugins_set_refuses_a_plugin_not_in_the_file(files, tmp_path):
    config, approvals = files
    with pytest.raises(settings_edit.Refused):
        settings_edit.set_plugin(config, approvals, "garage", "tools", "[]", home=tmp_path)


def test_plugins_set_takes_a_plain_word_as_text(files, tmp_path):
    config, approvals = files
    settings_edit.set_plugin(config, approvals, "vacuum", "timeout", "45", home=tmp_path)
    assert _read(config)["plugins"]["vacuum"]["timeout"] == 45


def test_machines_add_writes_a_machine_and_refuses_one_already_there(files):
    config, _ = files
    settings_edit.add_machine(config, "demo-laptop", "100.64.0.9")
    assert _read(config)["machines"]["demo-laptop"] == {"address": "100.64.0.9"}
    with pytest.raises(settings_edit.Refused):
        settings_edit.add_machine(config, "demo-laptop", "100.64.0.10")


def test_set_writes_only_the_listed_top_level_keys(files):
    config, _ = files
    settings_edit.set_top(config, "voice_instructions_file", "~/.config/claude-voice/voice-instructions.md")
    assert _read(config)["voice_instructions_file"].endswith("voice-instructions.md")
    with pytest.raises(settings_edit.Refused):
        settings_edit.set_top(config, "profile", "/tmp/x.toml")


def test_every_write_keeps_the_rest_of_the_file(files, tmp_path):
    config, approvals = files
    settings_edit.add_machine(config, "demo-laptop", "100.64.0.9")
    assert _read(config)["plugins"]["mister"]["args"][-1] == "--simulate"


def test_roles_add_and_remove(tmp_path):
    roles = tmp_path / "roles.toml"
    settings_edit.add_role(roles, "demo-builder", {"folder": "~/demo", "session": "demo-builder"})
    assert _read(roles)["roles"]["demo-builder"]["folder"] == "~/demo"
    with pytest.raises(settings_edit.Refused):
        settings_edit.add_role(roles, "demo-builder", {"folder": "~/other"})
    settings_edit.remove_role(roles, "demo-builder")
    assert "demo-builder" not in _read(roles).get("roles", {})
    with pytest.raises(settings_edit.Refused):
        settings_edit.remove_role(roles, "demo-builder")


@pytest.mark.parametrize("key", ["terminal_command", "command"])
def test_roles_add_refuses_a_start_line_of_its_own(tmp_path, key):
    with pytest.raises(settings_edit.Refused):
        settings_edit.add_role(tmp_path / "roles.toml", "x", {key: "sh -c x"})


# The roles cases are synthetic: no session could read the roles edits from the transcripts,
# so these are built from the documented role fields, with made-up names.


def test_roles_set_changes_one_field_of_a_role(tmp_path):
    roles = tmp_path / "roles.toml"
    settings_edit.add_role(roles, "demo-builder", {"folder": "~/demo"})
    settings_edit.set_role(roles, "demo-builder", "session", "demo-builder-2")
    assert _read(roles)["roles"]["demo-builder"] == {"folder": "~/demo", "session": "demo-builder-2"}


def test_roles_set_refuses_a_start_line_and_a_role_not_there(tmp_path):
    roles = tmp_path / "roles.toml"
    settings_edit.add_role(roles, "demo-builder", {"folder": "~/demo"})
    with pytest.raises(settings_edit.Refused):
        settings_edit.set_role(roles, "demo-builder", "terminal_command", "sh -c x")
    with pytest.raises(settings_edit.Refused):
        settings_edit.set_role(roles, "nobody", "folder", "~/x")


def test_a_role_from_the_template_keeps_the_templates_start_line(tmp_path):
    from claude_voice import roles as role_files

    roles = tmp_path / "roles.toml"
    built_in = next(iter(role_files.load(None)))
    settings_edit.set_role(roles, built_in, "folder", "~/demo")
    assert "terminal_command" not in _read(roles)["roles"][built_in]


def test_the_command_line_runs_each_settings_command(tmp_path):
    from claude_voice.cli import main

    env = {"HOME": str(tmp_path), "CLAUDE_VOICE_CONFIG": str(tmp_path / "c.toml")}
    (tmp_path / "c.toml").write_text(DEMO)
    assert main(["plugins", "set", "vacuum", "timeout", "45"], env=env) == 0
    assert main(["machines", "add", "demo-laptop", "100.64.0.9"], env=env) == 0
    assert main(["set", "voice_instructions_file", "~/v.md"], env=env) == 0
    assert main(["roles", "add", "demo", "folder=~/demo"], env=env) == 0
    assert main(["roles", "set", "demo", "session", "demo-2"], env=env) == 0
    assert main(["roles", "remove", "demo"], env=env) == 0
    assert main(["plugins", "set", "vacuum", "command", "sh"], env=env) == 1
    written = _read(tmp_path / "c.toml")
    assert written["plugins"]["vacuum"]["timeout"] == 45 and "demo-laptop" in written["machines"]


COMMENTED = """\
# my own notes at the top
voice_instructions_file = "~/old.md"  # the voice's text

# vacuum, simulated until the robot is back
[plugins.vacuum]
command = "uv"
args = ["run", "vacuum-mcp", "serve"]
cwd = "~/vacuum-mcp"
tools = [
    "vacuum_status",  # the first tool
    "vacuum_rooms",
]
timeout = 30  # seconds

[plugins.mister]
command = "uv"
args = ["run", "mister-mcp", "serve", "--simulate"]
cwd = "~/mister-mcp"
tools = ["mister_status"]
"""


def test_every_command_keeps_the_files_comments_and_order(tmp_path):
    config = tmp_path / "config.toml"
    config.write_text(COMMENTED)
    settings_edit.set_plugin(config, None, "mister", "timeout", "45", home=tmp_path)
    settings_edit.add_machine(config, "demo-laptop", "100.64.0.9")
    settings_edit.set_top(config, "voice_instructions_file", "~/new.md")
    text = config.read_text()
    for kept in ("# my own notes at the top", "# vacuum, simulated until the robot is back", "timeout = 30  # seconds"):
        assert kept in text, kept
    assert text.index("[plugins.vacuum]") < text.index("[plugins.mister]") < text.index("[machines.demo-laptop]")
    raw = _read(config)
    assert raw["plugins"]["mister"]["timeout"] == 45
    assert raw["voice_instructions_file"] == "~/new.md"


def test_a_role_removed_takes_only_its_own_lines(tmp_path):
    roles = tmp_path / "roles.toml"
    roles.write_text('# my roles\n[roles.a]\nfolder = "~/a"\n\n# the second\n[roles.b]\nfolder = "~/b"\n')
    settings_edit.remove_role(roles, "a")
    assert roles.read_text().startswith("# my roles\n") and "# the second" in roles.read_text()
    assert _read(roles) == {"roles": {"b": {"folder": "~/b"}}}


@pytest.mark.parametrize(
    "fields", [{"guard": False}, {"guard": "false"}, {"upstream": "someone/else"}, {"upstream": "x", "guard": True}]
)
def test_roles_add_never_switches_the_push_guard_off_or_points_it_elsewhere(tmp_path, fields):
    with pytest.raises(settings_edit.Refused):
        settings_edit.add_role(tmp_path / "roles.toml", "demo", {"folder": "~/demo", **fields})


@pytest.mark.parametrize(("key", "text"), [("guard", "false"), ("upstream", "someone/else")])
def test_roles_set_never_switches_the_push_guard_off_or_points_it_elsewhere(tmp_path, key, text):
    roles = tmp_path / "roles.toml"
    settings_edit.add_role(roles, "demo", {"folder": "~/demo"})
    with pytest.raises(settings_edit.Refused):
        settings_edit.set_role(roles, "demo", key, text)


def test_roles_set_may_switch_the_push_guard_on(tmp_path):
    roles = tmp_path / "roles.toml"
    settings_edit.add_role(roles, "demo", {"folder": "~/demo", "guard": True})
    settings_edit.set_role(roles, "demo", "guard", "true")
    assert _read(roles)["roles"]["demo"]["guard"] is True
