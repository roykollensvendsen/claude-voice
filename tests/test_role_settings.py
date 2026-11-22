"""A role's own settings file (builder-role-v1, with v1.1 c1 to c3 and f2).

A role entry in the owner's own roles file may name a Claude Code settings file under the bridge's
roles folder. The bridge lays the role's guard over it, adding and never taking away, and refuses a
file that would skip checks. Whatever the role's settings allow, a push that deletes a branch is
refused, written plainly or as `git -C <folder> push`.
"""

from __future__ import annotations

import json

import pytest

from claude_voice import guard, roles

UPSTREAM = "roykollensvendsen/claude-voice"
OWNERS = f"https://github.com/{UPSTREAM}"


def decide(command, mode):
    urls = {"origin": OWNERS}
    return guard.decide(
        command, UPSTREAM, mode == "own-clone", urls.get, lambda: "origin", only_deletes=mode == "no-deletes"
    )


# -- a push that deletes a branch is refused, in every mode (c3) -------------------------------

DELETES = [
    "git push origin :feat/x",
    "git push origin +:feat/x",
    "git push origin --delete feat/x",
    "git push -d origin feat/x",
    "git push origin feat/y :feat/x",
    "git push --prune origin",
    "git push --mirror origin",
    "git -C /home/someone/clone push origin :feat/x",
    "git -C /home/someone/clone push origin --delete feat/x",
    # git takes any unambiguous start of a long option (#459 review b1)
    "git push --del origin feat/x",
    "git push --dele origin feat/x",
    "git push --pru origin",
    "git push --mir origin",
    "git push --m origin",
    # a setting given on the command line deletes with no delete word (#459 review b2)
    "git -c remote.origin.push=:feat/x push origin",
    "git -c remote.origin.push=+:feat/x push -q origin",
    "git -c remote.origin.mirror=true push origin",
    "git -c Remote.Origin.Mirror=true push origin",
    "git --config-env=remote.origin.push=REFSPEC push origin",
    "git --config-env remote.origin.mirror=MIRROR push origin",
    "git -c push.default=matching push origin",
    # the shell's own quoting, read as #461 reads it, still shows the delete
    "'git' push origin :feat/x",
    '"git" -c remote.origin.mirror=true push origin',
    "$'git' push --del origin feat/x",
]


@pytest.mark.parametrize("mode", ["fork-only", "own-clone", "no-deletes"])
def test_an_alias_set_on_the_line_is_never_let_through(mode):
    """A setting given with -c is read for deletes and also keeps the guard from following an
    alias the same line may define: both rules read one list of the line's settings."""
    verdict = decide("git -c alias.zap='push origin :feat/x' zap", mode)
    assert verdict is not None


def test_without_the_guard_a_delete_behind_a_git_alias_is_refused(tmp_path):
    import subprocess

    def git(*args):
        subprocess.run(["git", "-C", str(tmp_path), *args], check=True, capture_output=True)

    git("init", "-q")
    git("remote", "add", "origin", OWNERS)
    git("config", "alias.zap", "push origin :feat/x")
    hook = {"tool_name": "Bash", "tool_input": {"command": "git zap"}, "cwd": str(tmp_path)}
    out = guard.answer(hook, UPSTREAM, only_deletes=True)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"


@pytest.mark.parametrize("flag", ["--d", "--del", "--delete", "--pru", "--prune", "--m", "--mir", "--mirror"])
def test_any_start_of_a_deleting_long_option_deletes(flag):
    assert guard._deletes(guard._pushes(f"git push {flag} origin feat/x")[0]) is True


@pytest.mark.parametrize(
    "words", ["-c remote.origin.push=:x", "-c remote.fork.mirror=true", "--config-env=push.default=V", "-c push.x=y"]
)
def test_a_deleting_setting_given_before_push_deletes(words):
    assert guard._deletes(guard._pushes(f"git {words} push origin")[0]) is True
    assert guard._deletes(guard._pushes("git -c user.name=x push origin feat/x")[0]) is False


@pytest.mark.parametrize("command", ["git push --dry-run origin feat/x", "git push --porcelain origin feat/x"])
def test_a_long_option_that_starts_no_delete_word_still_pushes(command):
    assert guard._deletes(guard._pushes(command)[0]) is False


@pytest.mark.parametrize("mode", ["fork-only", "own-clone", "no-deletes"])
def test_a_delete_set_up_in_the_clones_own_settings_is_refused(mode):
    asked = []

    def set_up(folder, remote):
        asked.append((folder, remote))
        return remote == "origin"

    urls = {"origin": OWNERS}
    verdict = guard.decide(
        "git push origin",
        UPSTREAM,
        mode == "own-clone",
        urls.get,
        lambda: "origin",
        only_deletes=mode == "no-deletes",
        deletes_set_up=set_up,
    )
    assert verdict is not None and verdict[0] == "deny" and "delete" in verdict[1]
    assert asked == [(None, "origin")]


def test_the_hook_reads_a_delete_set_up_earlier_in_the_clone(tmp_path):
    import subprocess

    def git(*args):
        subprocess.run(["git", "-C", str(tmp_path), *args], check=True, capture_output=True)

    git("init", "-q")
    git("remote", "add", "origin", OWNERS)
    hook = {"tool_name": "Bash", "tool_input": {"command": "git push origin"}, "cwd": str(tmp_path)}
    assert guard.answer(hook, UPSTREAM, only_deletes=True) == {}
    git("config", "--add", "remote.origin.push", ":refs/heads/feat/x")
    out = guard.answer(hook, UPSTREAM, only_deletes=True)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    git("config", "--unset-all", "remote.origin.push")
    git("config", "remote.origin.mirror", "true")
    out = guard.answer(hook, UPSTREAM, only_deletes=True)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_without_the_guard_a_push_the_guard_cannot_read_is_refused():
    """#459 review f1: an unreadable shape can hide a delete, so it is refused, naming the shape."""
    verdict = decide("git push origin feat/x; true", "no-deletes")
    assert verdict is not None and verdict[0] == "deny" and "git push <remote> <branch>" in verdict[1]


@pytest.mark.parametrize("mode", ["fork-only", "own-clone", "no-deletes"])
@pytest.mark.parametrize("command", DELETES)
def test_a_push_that_deletes_a_branch_is_refused_in_every_mode(command, mode):
    verdict = decide(command, mode)
    assert verdict is not None and verdict[0] == "deny" and "delete" in verdict[1]


@pytest.mark.parametrize(
    "command",
    [
        "git push origin feat/x",
        "git -C /home/someone/clone push origin feat/x",
        "git push -u origin feat/x",
        "git -C /home/someone/clone push --force-with-lease=feat/x:0a1b2c3d origin feat/x",
    ],
)
def test_without_the_guard_a_role_with_its_own_settings_still_pushes_its_branches(command):
    assert decide(command, "no-deletes") is None


def test_a_role_with_its_own_settings_and_no_guard_gets_the_delete_check(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    _file(home, "b.json", {"permissions": {"allow": ["Bash(git push:*)"]}})
    merged = json.loads(roles.settings_for(_role(home, "b.json", guard=False)))
    commands = [h["command"] for e in merged["hooks"]["PreToolUse"] for h in e["hooks"]]
    assert any("claude_voice.guard" in c and "--push-mode no-deletes" in c for c in commands)


# -- where the file may come from (c1) ---------------------------------------------------------


def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))  # where Windows looks for `~`
    (tmp_path / ".local/state/claude-voice/roles").mkdir(parents=True)
    return tmp_path


def _file(home, name, body):
    path = home / ".local/state/claude-voice/roles" / name
    path.write_text(json.dumps(body), encoding="utf-8")
    return path


def _role(home, name, guard=True):
    """A role entry naming the file, pinned to the file's bytes as they are now."""
    import hashlib

    path = home / ".local/state/claude-voice/roles" / name
    pin = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else "0" * 64
    return {
        "upstream": UPSTREAM,
        "guard": guard,
        "settings": str(path),
        "settings_sha256": pin,
        "push_mode": "own-clone",
    }


# -- the owner pins the file's bytes in the roles entry (#459 review b2) -----------------------


def test_a_settings_file_that_differs_from_the_owners_pin_is_refused(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    _file(home, "b.json", {"permissions": {"deny": ["Bash(gh pr merge:*)"]}})
    role = _role(home, "b.json")
    _file(home, "b.json", {"permissions": {"allow": ["Bash(*)"]}})
    with pytest.raises(LookupError, match="settings_sha256"):
        roles.settings_for(role)


def test_a_settings_file_with_no_pin_is_refused(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    _file(home, "b.json", {})
    role = {k: v for k, v in _role(home, "b.json").items() if k != "settings_sha256"}
    with pytest.raises(LookupError, match="settings_sha256"):
        roles.settings_for(role)


@pytest.mark.parametrize(
    "body",
    [
        {"permissions": ["Bash(*)"]},
        {"hooks": ["x"]},
        {"hooks": {"PreToolUse": "x"}},
        {"permissions": {"deny": "Bash(rm *)"}},
    ],
)
def test_a_settings_value_of_the_wrong_kind_names_the_fix(tmp_path, monkeypatch, body):
    """#459 review f3 (architect): a wrong type is refused with the fix, never a crash."""
    home = _home(tmp_path, monkeypatch)
    _file(home, "b.json", body)
    with pytest.raises(LookupError, match="must be"):
        roles.settings_for(_role(home, "b.json"))


def test_a_roles_own_settings_file_reaches_its_session(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    _file(home, "b.json", {"permissions": {"allow": ["Bash(git status)"], "deny": ["Bash(gh pr merge:*)"]}})
    merged = json.loads(roles.settings_for(_role(home, "b.json")))
    assert merged["permissions"]["allow"] == ["Bash(git status)"]
    assert merged["permissions"]["deny"] == ["Bash(gh pr merge:*)"]


def test_a_roles_settings_file_is_honoured_only_from_the_owners_own_roles_file(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    shipped = tmp_path / "shipped.toml"
    shipped.write_text("[roles.helper]\nsettings = '/x/y.json'\ntitle = 'Helper'\n", encoding="utf-8")
    monkeypatch.setattr(roles, "BUILT_IN", shipped)
    assert "settings" not in roles.load(None)["helper"]
    mine = tmp_path / "mine.toml"
    path = _file(home, "h.json", {})
    mine.write_text(f"[roles.helper]\nsettings = '{path}'\n", encoding="utf-8")
    assert roles.load(mine)["helper"]["settings"] == str(path)


def test_a_settings_file_outside_the_roles_folder_is_refused(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    outside = tmp_path / "elsewhere.json"
    outside.write_text("{}", encoding="utf-8")
    role = {**_role(home, "x"), "settings": str(outside)}
    with pytest.raises(LookupError, match="move the file under"):
        roles.settings_for(role)
    link = home / ".local/state/claude-voice/roles" / "link.json"
    link.symlink_to(outside)
    with pytest.raises(LookupError, match="move the file under"):
        roles.settings_for(_role(home, "link.json"))


def test_a_missing_settings_file_refuses_the_role(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    with pytest.raises(LookupError, match="gone.json"):
        roles.settings_for(_role(home, "gone.json"))


@pytest.mark.parametrize(
    "body",
    [
        {"permissions": {"defaultMode": "bypassPermissions"}},
        {"permissions": {"defaultMode": "acceptEdits"}},
        {"defaultMode": "bypassPermissions"},
        {"disableAllHooks": True},
        {"permissions": {"defaultMode": "dontAsk"}},
        {"skipDangerousModePermissionPrompt": True},
    ],
)
def test_a_settings_file_that_skips_checks_is_refused(tmp_path, monkeypatch, body):
    home = _home(tmp_path, monkeypatch)
    _file(home, "b.json", body)
    with pytest.raises(LookupError, match="remove"):
        roles.settings_for(_role(home, "b.json"))


# -- the merge only adds (c2) ------------------------------------------------------------------


def test_the_guard_hook_is_kept_beside_the_files_own_hooks(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    own = {"matcher": "Bash", "hooks": [{"type": "command", "command": "/usr/bin/true"}]}
    _file(home, "b.json", {"hooks": {"PreToolUse": [own]}})
    merged = json.loads(roles.settings_for(_role(home, "b.json")))
    commands = [h["command"] for e in merged["hooks"]["PreToolUse"] for h in e["hooks"]]
    assert "/usr/bin/true" in commands
    assert any("claude_voice.guard" in c and "own-clone" in c for c in commands)


def test_an_allow_in_the_file_never_removes_a_deny(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    rule = "Bash(git push origin main)"
    _file(home, "b.json", {"permissions": {"allow": [rule], "deny": [rule]}})
    merged = json.loads(roles.settings_for(_role(home, "b.json")))
    assert rule in merged["permissions"]["deny"]
    verdict = decide("git -C /c push origin :main", "no-deletes")
    assert verdict is not None and verdict[0] == "deny"


# -- every launch logs which file it used (f2) -------------------------------------------------


def test_each_launch_logs_the_settings_files_fingerprint(tmp_path, monkeypatch, caplog):
    import hashlib
    import logging

    home = _home(tmp_path, monkeypatch)
    path = _file(home, "b.json", {"permissions": {}})
    with caplog.at_level(logging.INFO, logger="claude_voice.roles"):
        roles.settings_for(_role(home, "b.json"), name="builder")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    assert any("builder" in r.getMessage() and digest in r.getMessage() for r in caplog.records)
