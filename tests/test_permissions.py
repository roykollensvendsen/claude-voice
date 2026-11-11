"""The permission rules for the voice repositories, merged into each one's local settings.

A Claude Code session in one of these repositories asks before every command it has
no rule for. `claude-voice permissions` gives each repository the same rules: what a
session may run without asking (looking at git, running the tests), and what it may
never run (deleting history, reading a secret). The owner's own rules are kept.
"""

import json
import os

import pytest

from claude_voice import permissions
from claude_voice.cli import main

RULES = permissions.Rules(allow=["Bash(git status*)", "Bash(uv run pytest*)"], deny=["Bash(git reset --hard*)"])


def settings_file(folder):
    return folder / ".claude" / "settings.local.json"


def with_settings(folder, content):
    file = settings_file(folder)
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(json.dumps(content), encoding="utf-8")
    return file


def read(folder):
    return json.loads(settings_file(folder).read_text(encoding="utf-8"))


def test_rules_already_in_the_settings_are_kept(tmp_path):
    with_settings(
        tmp_path,
        {"model": "opus", "permissions": {"allow": ["Bash(make*)"], "deny": ["Bash(rm -rf*)"], "ask": ["Bash(x)"]}},
    )
    permissions.apply(tmp_path, RULES)
    got = read(tmp_path)
    assert got["model"] == "opus"
    assert got["permissions"]["allow"] == ["Bash(make*)", "Bash(git status*)", "Bash(uv run pytest*)"]
    assert got["permissions"]["deny"] == ["Bash(rm -rf*)", "Bash(git reset --hard*)"]
    assert got["permissions"]["ask"] == ["Bash(x)"]


def test_a_rule_the_settings_already_have_is_not_added_again(tmp_path):
    with_settings(tmp_path, {"permissions": {"allow": ["Bash(git status*)"]}})
    outcome = permissions.apply(tmp_path, RULES)
    got = read(tmp_path)["permissions"]
    assert got["allow"].count("Bash(git status*)") == 1
    assert outcome.added_allow == 1 and outcome.added_deny == 1


def test_a_second_run_writes_nothing(tmp_path):
    with_settings(tmp_path, {"permissions": {"allow": ["Bash(make*)"]}})
    permissions.apply(tmp_path, RULES)
    file = settings_file(tmp_path)
    before = (file.read_bytes(), os.stat(file).st_ino, os.stat(file).st_mtime_ns)
    outcome = permissions.apply(tmp_path, RULES)
    assert (file.read_bytes(), os.stat(file).st_ino, os.stat(file).st_mtime_ns) == before
    assert outcome.added_allow == 0 and outcome.added_deny == 0
    assert "already has every rule" in outcome.line()


def test_the_backup_is_written_only_once(tmp_path):
    original = {"permissions": {"allow": ["Bash(make*)"]}}
    file = with_settings(tmp_path, original)
    first = permissions.apply(tmp_path, RULES)
    backup = file.with_name("settings.local.json.before-quality-rules")
    assert first.backed_up and json.loads(backup.read_text(encoding="utf-8")) == original
    permissions.apply(tmp_path, permissions.Rules(allow=["Bash(ls*)"], deny=[]))
    assert json.loads(backup.read_text(encoding="utf-8")) == original


def test_a_dry_run_writes_nothing(tmp_path):
    file = with_settings(tmp_path, {"permissions": {"allow": ["Bash(make*)"]}})
    before = file.read_bytes()
    outcome = permissions.apply(tmp_path, RULES, dry_run=True)
    assert file.read_bytes() == before
    assert not file.with_name("settings.local.json.before-quality-rules").exists()
    assert outcome.added_allow == 2 and "would add 2 allow and 1 deny" in outcome.line()


RETIRING = permissions.Rules(allow=RULES.allow, deny=RULES.deny, retired=["Bash(cp * /tmp/*)"])


def test_a_retired_allow_rule_is_removed(tmp_path):
    """The old installer allowed a copy into /tmp, which got round the secret rules."""
    with_settings(tmp_path, {"permissions": {"allow": ["Bash(make*)", "Bash(cp * /tmp/*)"]}})
    outcome = permissions.apply(tmp_path, RETIRING)
    assert read(tmp_path)["permissions"]["allow"] == ["Bash(make*)", *RULES.allow]
    assert outcome.removed_allow == 1 and outcome.added_allow == 2
    assert "removed 1 retired allow rule" in outcome.line()


def test_only_the_exact_retired_rule_is_removed(tmp_path):
    near = ["Bash(cp * /tmp/x)", "Bash(cp *)", "Bash(cp * /tmp/*) ", "bash(cp * /tmp/*)"]
    with_settings(tmp_path, {"permissions": {"allow": near, "deny": ["Bash(cp * /tmp/*)"]}})
    outcome = permissions.apply(tmp_path, RETIRING)
    got = read(tmp_path)["permissions"]
    assert got["allow"][: len(near)] == near and "Bash(cp * /tmp/*)" in got["deny"]
    assert outcome.removed_allow == 0 and "retired allow" not in outcome.line()


def test_a_dry_run_says_how_many_retired_rules_it_would_remove(tmp_path):
    file = with_settings(tmp_path, {"permissions": {"allow": [*RULES.allow, "Bash(cp * /tmp/*)"], "deny": RULES.deny}})
    before = file.read_bytes()
    outcome = permissions.apply(tmp_path, RETIRING, dry_run=True)
    assert file.read_bytes() == before
    assert outcome.line() == f"{tmp_path}: would add 0 allow and 0 deny rules, and remove 1 retired allow rule"


def test_removing_a_retired_rule_keeps_the_backup_already_written(tmp_path):
    original = {"permissions": {"allow": ["Bash(make*)"]}}
    file = with_settings(tmp_path, original)
    permissions.apply(tmp_path, RULES)
    settings = read(tmp_path)
    settings["permissions"]["allow"].append("Bash(cp * /tmp/*)")
    file.write_text(json.dumps(settings), encoding="utf-8")
    outcome = permissions.apply(tmp_path, RETIRING)
    assert outcome.removed_allow == 1 and not outcome.backed_up
    backup = file.with_name("settings.local.json.before-quality-rules")
    assert json.loads(backup.read_text(encoding="utf-8")) == original


def test_a_dry_run_does_not_create_a_missing_settings_file(tmp_path):
    permissions.apply(tmp_path, RULES, dry_run=True)
    assert not (tmp_path / ".claude").exists()


def test_a_missing_settings_file_is_created_with_the_rules(tmp_path):
    outcome = permissions.apply(tmp_path, RULES)
    assert read(tmp_path) == {"permissions": {"allow": RULES.allow, "deny": RULES.deny}}
    assert outcome.created and not outcome.backed_up
    assert not settings_file(tmp_path).with_name("settings.local.json.before-quality-rules").exists()


def test_a_settings_file_that_is_not_json_is_left_alone(tmp_path):
    file = settings_file(tmp_path)
    file.parent.mkdir()
    file.write_text("{ not json", encoding="utf-8")
    with pytest.raises(permissions.PermissionsError, match="not JSON"):
        permissions.apply(tmp_path, RULES)
    assert file.read_text(encoding="utf-8") == "{ not json"


def test_a_folder_that_does_not_exist_is_refused(tmp_path):
    with pytest.raises(permissions.PermissionsError, match="not a folder"):
        permissions.apply(tmp_path / "nowhere", RULES)


# -- the rules this repository ships ---------------------------------------------------


def test_the_shipped_rules_have_no_duplicates_and_nothing_both_allowed_and_denied():
    rules = permissions.load()
    assert len(rules.allow) == len(set(rules.allow)) and len(rules.deny) == len(set(rules.deny))
    assert not set(rules.allow) & set(rules.deny)
    assert "Bash(git status*)" in rules.allow and "Bash(git reset --hard*)" in rules.deny


def test_every_command_allowed_to_read_a_file_is_refused_every_secret_file():
    """A secret is protected only if no allowed reader can reach it by its path."""
    rules = permissions.load()
    table = json.loads(permissions.TABLE.read_text(encoding="utf-8"))
    readers, files = table["secrets"]["readers"], table["secrets"]["files"]
    assert "*.config/claude-voice/env*" in files
    allowed = [reader for reader in readers if f"Bash({reader} *)" in rules.allow or f"Bash({reader}*)" in rules.allow]
    assert {"cat", "head", "tail", "sed -n", "grep"} <= set(allowed)
    for reader in readers:
        for path in files:
            assert f"Bash({reader} {path})" in rules.deny


def test_copying_or_printing_a_secret_file_is_refused():
    """A copy puts the secret where no rule guards it; these print a file's bytes."""
    rules = permissions.load()
    for reader in ("cp", "rsync", "base64", "xxd", "od"):
        for path in ("*.config/claude-voice/env*", "*secrets.yaml*", "*.claude/.credentials.json*"):
            assert f"Bash({reader} {path})" in rules.deny


def test_the_copy_into_tmp_the_old_installer_allowed_is_retired():
    rules = permissions.load()
    assert rules.retired == ["Bash(cp * /tmp/*)"]
    assert not set(rules.retired) & set(rules.allow)


#: The rules the first hand-run installer refused. The shipped rules may refuse more, never less:
#: dropping one is a permission change the owner decides, not a side effect of moving the rules.
INSTALLED_BEFORE = (
    "Bash(cat *.config/claude-voice/env*)",
    "Bash(cat *.config/dreame*)",
    "Bash(cat *.config/tv-mcp*)",
    "Bash(cat *secrets.yaml*)",
    "Bash(git branch * -D*)",
    "Bash(git branch * -d*)",
    "Bash(git branch --delete*)",
    "Bash(git branch -D*)",
    "Bash(git branch -d*)",
    "Bash(git branch -f*)",
    "Bash(git checkout -- *)",
    "Bash(git clean -f*)",
    "Bash(git reset --hard*)",
    "Bash(git worktree remove* -f*)",
    "Bash(git worktree remove*--force*)",
    "Bash(grep *.config/claude-voice/env*)",
    "Bash(grep *.config/dreame*)",
    "Bash(grep *.config/tv-mcp*)",
    "Bash(grep *secrets.yaml*)",
    "Bash(head *.config/dreame*)",
    "Bash(head *.config/tv-mcp*)",
    "Bash(head *secrets.yaml*)",
    "Bash(sed -n *.config/*)",
    "Bash(sed -n *secrets*)",
    "Bash(tail *.config/dreame*)",
    "Bash(tail *.config/tv-mcp*)",
    "Bash(tail *secrets.yaml*)",
)


def test_everything_refused_before_is_still_refused():
    rules = permissions.load()
    assert [rule for rule in INSTALLED_BEFORE if rule not in rules.deny] == []


def test_the_shipped_rules_name_no_person_host_or_home_folder():
    text = permissions.TABLE.read_text(encoding="utf-8")
    for private in ("/home/", "/users/", "~/", "roykollensvendsen", "192.168.", "100.", ".local/"):
        assert private not in text.lower(), private


# -- which folders, and the command ----------------------------------------------------


def config_naming(tmp_path, *folders, plugin=None):
    # Literal TOML strings, so a Windows path's backslashes are not read as escapes.
    lines = ["[paths]", f"claude_voice = '{folders[0]}'", f"claude_voice_ui = '{folders[1]}'"]
    if plugin:
        lines += ["[plugins.garage]", 'command = "uv"', f"cwd = '{plugin}'", 'tools = ["garage_status"]']
    file = tmp_path / "config.toml"
    file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"HOME": str(tmp_path), "CLAUDE_VOICE_CONFIG": str(file)}


def test_with_no_folders_named_the_bridge_and_plugin_clones_from_the_settings_are_used(tmp_path, capsys):
    voice, ui, garage = tmp_path / "voice", tmp_path / "ui", tmp_path / "garage"
    for folder in (voice, garage):
        folder.mkdir()
    env = config_naming(tmp_path, voice, ui, plugin=garage)
    assert main(["permissions"], env=env) == 0
    assert "Bash(git status*)" in read(voice)["permissions"]["allow"]
    assert "Bash(git status*)" in read(garage)["permissions"]["allow"]
    assert not ui.exists()  # a clone that is not there is skipped, not made
    out = capsys.readouterr().out
    assert str(voice.resolve()) in out and str(garage.resolve()) in out


def test_the_command_takes_folders_and_a_dry_run(tmp_path, capsys):
    assert main(["permissions", "--dry-run", str(tmp_path)], env={"HOME": str(tmp_path)}) == 0
    assert not (tmp_path / ".claude").exists()
    assert f"{tmp_path}: would add" in capsys.readouterr().out


def test_the_command_says_which_folder_it_could_not_change(tmp_path, capsys):
    missing = tmp_path / "nowhere"
    assert main(["permissions", str(missing), str(tmp_path)], env={"HOME": str(tmp_path)}) == 1
    captured = capsys.readouterr()
    assert f"{missing} is not a folder" in captured.err
    assert f"{tmp_path}: added" in captured.out


def test_with_no_folders_named_and_none_in_the_settings_it_says_how_to_name_them(tmp_path, capsys):
    env = config_naming(tmp_path, tmp_path / "gone", tmp_path / "gone-too")
    assert main(["permissions"], env=env) == 1
    assert "name the folders" in capsys.readouterr().err
