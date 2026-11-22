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
]


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
    (tmp_path / ".local/state/claude-voice/roles").mkdir(parents=True)
    return tmp_path


def _file(home, name, body):
    path = home / ".local/state/claude-voice/roles" / name
    path.write_text(json.dumps(body), encoding="utf-8")
    return path


def _role(home, name, guard=True):
    return {
        "upstream": UPSTREAM,
        "guard": guard,
        "settings": str(home / ".local/state/claude-voice/roles" / name),
        "push_mode": "own-clone",
    }


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
