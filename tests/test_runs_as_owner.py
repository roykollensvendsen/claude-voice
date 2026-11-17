"""No session edits what later runs as the owner (runs-as-owner v1.3).

A login script, a timer, a git hook or Claude Code's own settings run later, as the owner,
with no session watching. These tests pin the deny group in permissions.json: every path
kind is refused to the Edit tool and to every writing command, the reads stay open, and
the stated carve-outs hold.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from test_permissions import decision

from claude_voice import permissions

HOME = Path("/home/someone")
REPO = HOME / "code" / "some-repo"
#: The group's paths are POSIX home paths. They don't cover a native Windows session yet;
#: docs/permissions.md says so. The session hook's tests below still run on Windows.
NOT_WINDOWS = "the runs-as-owner rules don't cover native Windows sessions yet"
posix_rules = pytest.mark.skipif(sys.platform == "win32", reason=NOT_WINDOWS)


@pytest.fixture(scope="module")
def rules():
    if sys.platform == "win32":
        pytest.skip(NOT_WINDOWS)
    return permissions.for_folder(permissions.load(), REPO, home=HOME, uid=1000)


# One path of each kind in §2 and §7, as the owner would write it under home.
KINDS = [
    ".config/systemd/user/x.service",
    ".config/environment.d/x.conf",
    ".local/share/systemd/user/x.timer",
    ".config/autostart/x.desktop",
    ".local/share/applications/x.desktop",
    ".bashrc",
    ".bash_profile",
    ".bash_login",
    ".profile",
    ".zshrc",
    ".zprofile",
    ".zshenv",
    ".config/fish/config.fish",
    ".pam_environment",
    ".xprofile",
    ".local/bin/x",
    "bin/x",
    ".gitconfig",
    ".config/git/config",
    ".ssh/config",
    ".ssh/authorized_keys",
    ".ssh/rc",
    ".tmux.conf",
    ".config/tmux/tmux.conf",
    ".claude/settings.json",
    ".claude/settings.local.json",
    ".claude/hooks/x.sh",
    ".claude/skills/x/SKILL.md",
    ".claude/plugins/x/plugin.json",
    ".claude.json",
    ".config/claude-voice/config.toml",
    ".config/claude-voice/roles.toml",
    ".config/claude-voice/machines/x.toml",
    ".config/claude-voice/plugins/x.toml",
    ".config/gh/config.yml",
    ".config/uv/uv.toml",
    ".config/pip/pip.conf",
    ".pip/pip.conf",
    ".npmrc",
    ".vimrc",
    ".config/nvim/init.lua",
    ".emacs",
    ".emacs.d/init.el",
]


@pytest.mark.parametrize("name", KINDS)
def test_every_path_that_later_runs_as_the_owner_is_refused_to_the_edit_tools(rules, name):
    for tool in ("Edit", "Write"):
        assert f"{tool}(~/{name})" in rules.deny or any(_covers(rule, f"{tool}(~/{name})") for rule in rules.deny), (
            tool,
            name,
        )
        assert any(_covers(rule, f"{tool}(/{HOME}/{name})") for rule in rules.deny), (tool, name)


@pytest.mark.parametrize("name", KINDS)
@pytest.mark.parametrize("form", ["~/{}", "/home/someone/{}", "$HOME/{}"])
@pytest.mark.parametrize(
    "verb",
    [
        "tee {}",
        "tee -a {}",
        "cp x {}",
        "mv x {}",
        "install x {}",
        "ln -s x {}",
        "rsync x {}",
        "sed -i s/a/b/ {}",
        "dd if=x of={}",
        "truncate -s 0 {}",
        "chmod +x {}",
        "touch {}",
    ],
)
def test_every_writing_command_into_such_a_path_is_refused(rules, name, form, verb):
    command = verb.format(form.format(name))
    assert decision(rules, command) == "refused", command


@pytest.mark.parametrize(
    "name", [".git/hooks/pre-commit", ".git/config", ".claude/settings.json", ".claude/settings.local.json"]
)
def test_hooks_and_config_of_any_clone_are_refused(rules, name):
    for where in (f"{REPO}/{name}", f"/tmp/elsewhere/{name}", f"{REPO}/.claude/worktrees/w/{name}"):
        assert decision(rules, f"tee {where}") == "refused", where
        assert any(_covers(rule, f"Edit(/{where})") for rule in rules.deny), where


@pytest.mark.parametrize("name", [".githooks/pre-commit", ".npmrc", "uv.toml", "pip.conf", ".envrc"])
def test_what_runs_from_the_owners_own_checkout_is_refused_there_but_not_in_a_worktree(rules, name):
    assert decision(rules, f"tee {REPO}/{name}") == "refused"
    assert any(_covers(rule, f"Edit(/{REPO}/{name})") for rule in rules.deny)
    worktree = f"{REPO}/.claude/worktrees/w/{name}"
    assert decision(rules, f"tee {worktree}") != "refused"
    assert not any(_covers(rule, f"Edit(/{worktree})") for rule in rules.deny)


@pytest.mark.parametrize("command", ["crontab", "crontab -l", "crontab -e", "crontab -r", "crontab x", "crontab -"])
def test_every_form_of_crontab_is_refused(rules, command):
    assert decision(rules, command) == "refused"


@pytest.mark.parametrize(
    "command",
    [
        "systemctl --user edit x",
        "systemctl --user link /tmp/x.service",
        "systemctl --user enable /tmp/x.service",
        "systemctl --user enable ~/x.service",
        "systemctl --user set-environment A=b",
        "systemctl --user import-environment PATH",
    ],
)
def test_systemd_commands_that_install_or_change_what_runs_are_refused(rules, command):
    assert decision(rules, command) == "refused"


@pytest.mark.parametrize(
    "command",
    [
        "git config --global core.hooksPath ~/h",
        "git config --system core.hooksPath /h",
        "git config -f x core.hooksPath /h",
        "git config --file x core.hooksPath /h",
        "git config core.fsmonitor x",
        "git config core.sshCommand x",
        "git config core.pager x",
        "git config core.editor x",
        "git config credential.helper x",
        "git config alias.x !sh",
        "git config include.path x",
        "git config includeIf.gitdir:/x.path y",
        "git config filter.x.clean y",
        "git config diff.x.textconv y",
        "git config gpg.program x",
        "git config --global alias.x y",
    ],
)
def test_git_config_of_a_key_that_runs_code_is_refused(rules, command):
    assert decision(rules, command) == "refused"


@pytest.mark.parametrize(
    "command",
    [
        "cd ~/.config/systemd/user",
        "cd /home/someone/.config/systemd/user",
        "pushd ~/.local/bin",
        "git -C ~/.config/git config x y",
        "make -C ~/.config/systemd/user x",
        "tar -C ~/.config/autostart -xf x.tar",
        "cd ~/.claude/hooks",
    ],
)
def test_moving_into_a_protected_folder_is_refused(rules, command):
    assert decision(rules, command) == "refused"


@pytest.mark.parametrize(
    "command",
    [
        "cat ~/.bashrc",
        "head ~/.config/systemd/user/x.service",
        "systemctl --user status claude-voice",
        "systemctl --user list-timers",
        "systemctl --user list-units",
        "systemctl --user show claude-voice",
        "systemctl --user cat claude-voice",
        "systemctl --user restart claude-voice",
        "git config --get core.hooksPath",
        "git push -u origin feat/x",
        "git branch --set-upstream-to origin/feat/x",
        "git worktree add .claude/worktrees/w",
        "git remote add up https://example.invalid/x.git",
        "cd .claude/skills",
        "cd ~",
        "sed -i s#~/.local/bin#/opt/x# scratch.sh",
        "tee ~/.claude/projects/p/memory/note.md",
        "tee ~/.config/claude-voice/voice-instructions.md",
    ],
)
def test_reads_git_s_own_writes_and_the_stated_carve_outs_are_not_refused(rules, command):
    assert decision(rules, command) != "refused", command


@pytest.mark.parametrize(
    "path",
    [
        "~/.claude/projects/p/memory/note.md",
        "~/.config/claude-voice/voice-instructions.md",
        f"/{REPO}/.claude/skills/x/SKILL.md",
    ],
)
def test_memory_voice_text_and_a_repos_own_skills_stay_editable(rules, path):
    assert not any(_covers(rule, f"Edit({path})") for rule in rules.deny), path


@posix_rules
def test_the_group_is_written_with_the_real_home_folder():
    rules = permissions.for_folder(permissions.load(), REPO, home=HOME, uid=1000)
    settings = permissions.merged({}, rules)
    deny = settings["permissions"]["deny"]
    assert "Edit(//home/someone/.bashrc)" in deny
    assert "Bash(tee /home/someone/.bashrc)" in deny
    assert not any("{home}" in rule or "{folder}" in rule for rule in deny)


def _covers(rule: str, wanted: str) -> bool:
    """Whether an Edit or Write rule's path pattern covers a path, `**` across folders and `*`
    within one, the way Claude Code reads a path rule."""
    import re

    tool, _, pattern = rule.partition("(")
    want_tool, _, path = wanted.partition("(")
    if tool != want_tool or not rule.endswith(")"):
        return False
    pattern, path = pattern[:-1], path[:-1]
    regex = re.escape(pattern).replace(r"\*\*", "\0").replace(r"\*", "[^/]*").replace("\0", ".*")
    return re.fullmatch(regex, path) is not None


# -- the local hooksPath, judged by the session hook (v1.4 §3 (c)) ---------------------------


def _hook(command: str) -> str | None:
    from claude_voice import session_hook

    given = {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(REPO), "session_id": "s"}
    return session_hook.decision(given, tmp_root=Path("/nonexistent"), repos=[], terms=None)


@pytest.mark.parametrize(
    "command",
    [
        "git config core.hooksPath /tmp/x",
        "git config core.hooksPath .githooks/../x",
        "git config core.hooksPath '.githooks '",
        "git config --local core.hooksPath x",
        "git -c core.hooksPath=/tmp/x commit -m y",
        "git -c core.fsmonitor=x status",
        "git config core.hookspath /tmp/x",
    ],
)
def test_the_session_hook_refuses_any_hooks_path_but_the_tracked_folder(command):
    assert _hook(command) == "deny", command


@pytest.mark.parametrize("command", ["git config core.hooksPath .githooks", "git config --get core.hooksPath"])
def test_the_session_hook_lets_the_contributing_setup_line_through(command):
    assert _hook(command) != "deny", command


@pytest.mark.parametrize(
    "command",
    [
        "git --config-env=core.hooksPath=H commit -m y",
        "git --config-env core.hooksPath=H commit -m y",
        "git --config-env=core.sshCommand=S fetch",
        "git config core.askPass x",
        "git config core.gitProxy x",
        "git config sequence.editor x",
        "git config diff.external x",
        "git config diff.x.command y",
        "git config difftool.x.cmd y",
        "git config mergetool.x.cmd y",
        "git config merge.x.driver y",
        "git config gpg.ssh.program y",
        "git config remote.origin.uploadpack y",
        "git -c remote.origin.receivepack=y push",
    ],
)
def test_a_setting_taken_from_the_environment_is_refused_for_every_key_that_runs_code(command):
    assert _hook(command) == "deny", command


def test_a_setting_from_the_environment_that_runs_nothing_passes():
    assert _hook("git --config-env=user.name=N commit -m y") != "deny"
