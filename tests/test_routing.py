"""Which approvals must always reach the owner, and which a manager could one day answer."""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from claude_voice import routing

DAY = datetime(2026, 11, 17, 12, 0, tzinfo=UTC)  # 13.00 in Oslo
NIGHT = datetime(2026, 11, 17, 22, 30, tzinfo=UTC)  # 23.30 in Oslo


@pytest.fixture
def place(tmp_path: Path) -> routing.Place:
    repo = tmp_path / "repo"
    (repo / ".claude" / "worktrees" / "wt").mkdir(parents=True)
    (repo / "src").mkdir()
    (repo / "src" / "a.py").write_text("x = 1\n")
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    return routing.Place(repo=repo, scratchpad=scratch, home=tmp_path / "home", owner="owner-user")


def bash(command: str, place: routing.Place, **kwargs) -> routing.Routing:
    return routing.sort("Bash", {"command": command}, place=place, now=kwargs.pop("now", DAY), **kwargs)


def line_of(command: str, place: routing.Place, **kwargs) -> int | None:
    return bash(command, place, **kwargs).line


# -- every row goes to the owner in this step ---------------------------------------------


def test_every_prompt_goes_to_the_owner_while_no_manager_answers(place):
    for tool, given in [("Read", {"file_path": str(place.repo / "src" / "a.py")}), ("Bash", {"command": "git status"})]:
        out = routing.sort(tool, given, place=place, now=DAY)
        assert out.to == "roy" and out.shape is True and out.line is None


# -- the fixed list, one test per line ----------------------------------------------------


@pytest.mark.parametrize(
    "command",
    [
        "cat /home/x/.config/claude-voice/env",
        "gh auth token",
        "git credential fill",
        "ssh-keygen -t ed25519",
        "ssh-add -l",
        "export API_KEY=abc",
        "cv set password=hunter2",
    ],
)
def test_line_1_secrets_go_to_the_owner(place, command):
    assert line_of(command, place) == 1


def test_line_1_a_tool_input_setting_a_secret_goes_to_the_owner(place):
    out = routing.sort("mcp__x__set_value", {"name": "github", "token": "abc"}, place=place, now=DAY)
    assert out.line == 1


@pytest.mark.parametrize(
    "command",
    [
        "git push -f origin feat/x",
        "git push --force origin feat/x",
        "git push -fu origin feat/x",
        "git push --force=yes origin feat/x",
        "git push origin +feat/x",
        "git push --force-with-lease=feat/x:abc123 origin feat/x",
    ],
)
def test_line_2_force_pushes_go_to_the_owner(place, command):
    assert line_of(command, place) == 2


def test_line_2_a_lease_on_the_sessions_own_branch_goes_to_the_owner_for_now(place):
    """Step 2 lets the manager answer a lease on the session's own recorded branch; no record exists yet."""
    assert line_of("git push --force-with-lease=feat/mine:0a2e85f origin feat/mine", place) == 2


@pytest.mark.parametrize("tool", ["mcp__oda__checkout", "mcp__shop__place_order", "mcp__pay__payment_send"])
def test_line_3_money_goes_to_the_owner(place, tool):
    assert routing.sort(tool, {}, place=place, now=DAY).line == 3


def test_line_3_a_plugin_marked_money_goes_to_the_owner_for_now(place):
    """Step 2 reads the manifest's money mark; until then no plugin tool is a shape at all."""
    out = routing.sort("mcp__claude-voice__fuel_price", {}, place=place, now=DAY)
    assert out.to == "roy" and out.shape is False


@pytest.mark.parametrize(
    "command",
    [
        "gh pr create -R someone-else/repo --title t --body b",
        "gh issue comment 5 --repo someone-else/repo --body b",
        "gh api -X POST repos/someone-else/repo/issues",
        "gh api repos/someone-else/repo/issues -f title=t",
        "git push https://github.com/someone-else/repo HEAD:feat/x",
    ],
)
def test_line_4_public_writes_outside_the_owners_account_go_to_the_owner(place, command):
    assert line_of(command, place) == 4


@pytest.mark.parametrize(
    "command",
    ["claude-voice permissions --accept", "cv roles", "claude config set x", "git config user.name x", "chmod 777 f"],
)
def test_line_5_permissions_and_configuration_go_to_the_owner(place, command):
    assert line_of(command, place) == 5


@pytest.mark.parametrize("name", [".claude/settings.json", ".claude/settings.local.json", "CLAUDE.md", "AGENTS.md"])
def test_line_5_editing_the_rules_goes_to_the_owner(place, name):
    out = routing.sort("Edit", {"file_path": str(place.repo / name)}, place=place, now=DAY)
    assert out.line == 5 and out.shape is False


@pytest.mark.parametrize("tool", ["mcp__claude-voice__garage_confirm", "mcp__claude-voice__plugins_approve"])
def test_line_6_plugin_approval_goes_to_the_owner(place, tool):
    assert routing.sort(tool, {}, place=place, now=DAY).line == 6


def test_line_6_approving_a_plugin_by_command_goes_to_the_owner(place):
    assert line_of("claude-voice plugins approve garage", place) == 6


def test_line_7_the_owners_own_list_adds_to_what_reaches_the_owner(place):
    assert line_of("git status", place, also=["git status*"]) == 7
    assert line_of("git status", place, also=[]) is None


@pytest.mark.parametrize(
    "command",
    [
        "git push origin --delete feat/x",
        "git push origin :feat/x",
        "git branch -D feat/x",
        "rm -r build",
        "rm -rf /tmp/x",
        "gh pr merge 12 --rebase",
        "claude-voice restart",
    ],
)
def test_line_8_the_standing_rules_go_to_the_owner(place, command):
    assert line_of(command, place) == 8


@pytest.mark.parametrize("tool", ["mcp__claude-voice__other-laptop_send_task", "mcp__claude-voice__plugin_restart"])
def test_line_8_another_machine_or_a_plugin_restart_goes_to_the_owner(place, tool):
    out = routing.sort(tool, {}, place=place, now=DAY, machines=["other-laptop"])
    assert out.line == 8


def test_line_8_merging_any_pull_request_goes_to_the_owner_for_now(place):
    """Step 2 lets the manager merge the session's own PR by rebase in the day; no record of it exists yet."""
    assert line_of("gh pr merge 331 --rebase", place) == 8


@pytest.mark.parametrize(
    "command",
    [
        "bash -c 'ls'",
        "sh -c ls",
        "eval ls",
        "xargs rm",
        "python -c 1",
        "python3 -c 1",
        "node -e 1",
        "cat <<EOF",
        "git status && bash -c ls",
        "ls | sh",
        "source ./x.sh",
        ". ./x.sh",
    ],
)
def test_line_9_what_cannot_be_read_safely_goes_to_the_owner(place, command):
    assert line_of(command, place) == 9


def test_line_9_a_cut_input_and_an_unknown_tool_go_to_the_owner(place):
    assert routing.sort("Bash", {"command": "git status"}, place=place, now=DAY, cut=True).line == 9
    assert routing.sort("SomeNewTool", {}, place=place, now=DAY).line == 9


@pytest.mark.parametrize(
    "command",
    [
        "git push origin HEAD:main",
        "git push origin main",
        "git push origin refs/heads/main",
        "git push origin feat/x:master",
        "git push origin voice",
        "gh pr merge 12 --admin --rebase",
        "gh api -X PUT repos/owner-user/repo/branches/main/protection",
    ],
)
def test_line_10_main_and_admin_go_to_the_owner(place, command):
    assert 10 in bash(command, place, run_branches=["voice"]).lines


@pytest.mark.parametrize(
    "command", ["tmux send-keys -t x y", "tmux paste-buffer", "tmux load-buffer f", "screen -X stuff y"]
)
def test_line_11_typing_into_another_pane_goes_to_the_owner(place, command):
    assert line_of(command, place) == 11


def test_line_12_every_device_tool_goes_to_the_owner_for_now(place):
    """Step 2 reads which plugin tools are read only; until then every plugin tool goes to the owner."""
    for tool in ("mcp__claude-voice__garage_request", "mcp__claude-voice__weather_now"):
        out = routing.sort(tool, {}, place=place, now=DAY)
        assert out.to == "roy" and out.shape is False


@pytest.mark.parametrize("path", [".local/state/claude-voice/sessions.toml", ".local/state/claude-voice/ledger.jsonl"])
def test_line_13_the_bridges_state_goes_to_the_owner(place, path):
    out = routing.sort("Write", {"file_path": str(place.home / path)}, place=place, now=DAY)
    assert out.line == 13


@pytest.mark.parametrize(
    "command", ["systemctl --user enable x", "crontab -e", "at now", "git config core.hooksPath h"]
)
def test_line_14_start_up_and_identity_go_to_the_owner(place, command):
    assert 14 in bash(command, place).lines


@pytest.mark.parametrize("path", [".bashrc", ".profile", ".ssh/config", ".gitconfig", ".config/systemd/user/x"])
def test_line_14_start_up_and_identity_files_go_to_the_owner(place, path):
    assert 14 in routing.sort("Edit", {"file_path": str(place.home / path)}, place=place, now=DAY).lines


def test_line_14_a_git_hook_goes_to_the_owner(place):
    assert (
        routing.sort("Write", {"file_path": str(place.repo / ".git/hooks/pre-commit")}, place=place, now=DAY).line == 14
    )


def test_line_15_a_merge_at_night_goes_to_the_owner(place):
    assert 15 in bash("gh pr merge 12 --rebase", place, now=NIGHT).lines
    assert 15 not in bash("gh pr merge 12 --rebase", place, now=DAY).lines


def test_line_16_a_command_outside_the_sandbox_goes_to_the_owner(place):
    out = routing.sort("Bash", {"command": "ls", "dangerouslyDisableSandbox": True}, place=place, now=DAY)
    assert out.line == 16


@pytest.mark.parametrize(
    "command",
    ["gh gist create f", "gh repo create x --public", "gh repo edit --visibility public", "gh release create v1"],
)
def test_line_17_making_something_public_goes_to_the_owner(place, command):
    assert line_of(command, place) == 17


@pytest.mark.parametrize(
    "command",
    [
        "pip install x",
        "uv add x",
        "uv pip install x",
        "uv tool install x",
        "npm install x",
        "npm i x",
        "npx cowsay",
        "uvx ruff",
        "cargo install x",
        "git clone https://github.com/someone-else/x",
    ],
)
def test_line_18_installs_from_the_internet_go_to_the_owner(place, command):
    assert line_of(command, place) == 18


@pytest.mark.parametrize(
    "command",
    ["kill 1", "pkill x", "killall x", "systemctl stop x", "systemctl restart x", "docker stop x", "tmux kill-session"],
)
def test_line_19_stopping_processes_goes_to_the_owner(place, command):
    assert 19 in bash(command, place).lines


def test_every_line_of_the_fixed_list_has_a_test():
    names = {name for name in globals() if name.startswith("test_line_")}
    assert {int(name.split("_")[2]) for name in names} == set(range(1, 20))
    assert [number for number, _ in routing.FIXED] == list(range(1, 20))


# -- the shapes a manager could one day answer ---------------------------------------------


@pytest.mark.parametrize(
    "command",
    [
        "git status",
        "git log --oneline -5",
        "git diff HEAD~1",
        "git fetch -q origin",
        "git add src/a.py",
        "git commit -q --fixup HEAD",
        "git switch -q feat/x",
        "git rebase -q origin/main",
        "git worktree add wt",
        "gh pr view 3",
        "gh pr checks 3",
        "gh run view 5",
        "uv run pytest -q tests",
        "uv run ruff check src",
        "uv run mypy src",
        "uv sync",
        "ls src",
        "cat src/a.py",
        "head -n 5 src/a.py",
        "grep -n x src/a.py",
        "rg x src",
        "find src -name a.py",
        "wc -l src/a.py",
        "sort src/a.py",
        "jq . src/a.py",
        "mkdir -p src/b",
        "cp src/a.py src/b.py",
        "mv src/a.py src/c.py",
        "git status && git diff",
        "git fetch -q origin; git status",
        "claude-voice work",
        "claude-voice tidy",
    ],
)
def test_a_command_of_the_allowed_shapes_is_one(place, command):
    out = bash(command, place)
    assert (out.shape, out.line) == (True, None), command


@pytest.mark.parametrize(
    "command",
    [
        "git push origin feat/mine",  # step 2: the session's own recorded branch
        "gh pr create --title t --body b",  # step 2: the session's own PR
        "gh pr comment 3 --body b",
        "git rebase --exec 'make' origin/main",
        "git rebase -x make origin/main",
        "uv run python x.py",
        "uv run some-tool",
        "find src -exec rm {} ;",
        "find src -delete",
        "find src -fprint out",
        "find src -fls out",
        "find src -ok rm",
        "sort -o out src/a.py",
        "cat /etc/passwd",
        "cp src/a.py /tmp/elsewhere",
        "cp src/a.py .git/hooks/pre-commit",
        "mv src/a.py .github/workflows/x.yml",
        "curl https://example.com",
        "ls | grep x",
    ],
)
def test_a_command_of_no_allowed_shape_goes_to_the_owner(place, command):
    assert bash(command, place).shape is False, command


@pytest.mark.parametrize(
    ("tool", "inside"),
    [
        ("Read", "src/a.py"),
        ("Grep", "src"),
        ("Glob", "src"),
        ("Edit", "src/a.py"),
        ("Write", ".claude/worktrees/wt/b.py"),
        ("NotebookEdit", "src/n.ipynb"),
    ],
)
def test_a_file_tool_inside_the_sessions_own_repo_is_a_shape(place, tool, inside):
    key = "path" if tool in ("Grep", "Glob") else ("notebook_path" if tool == "NotebookEdit" else "file_path")
    assert routing.sort(tool, {key: str(place.repo / inside)}, place=place, now=DAY).shape is True


def test_reading_the_scratchpad_is_a_shape_and_writing_it_is_not(place):
    target = str(place.scratchpad / "notes.txt")
    assert routing.sort("Read", {"file_path": target}, place=place, now=DAY).shape is True
    assert routing.sort("Write", {"file_path": target}, place=place, now=DAY).shape is False


@pytest.mark.parametrize("inside", [".git/config", ".github/workflows/x.yml"])
def test_editing_git_or_the_workflows_is_no_shape(place, inside):
    assert routing.sort("Edit", {"file_path": str(place.repo / inside)}, place=place, now=DAY).shape is False


def test_a_file_outside_the_sessions_areas_is_no_shape(place, tmp_path):
    other = tmp_path / "other-programme" / "x.py"
    assert routing.sort("Read", {"file_path": str(other)}, place=place, now=DAY).shape is False


def test_only_three_bridge_tools_are_a_shape_by_name(place):
    for name in ("health", "org_chart", "list_projects"):
        assert routing.sort(f"mcp__claude-voice__{name}", {}, place=place, now=DAY).shape is True
    for name in ("list_pending_approvals", "read_session_output", "session_recap", "search_session_history"):
        assert routing.sort(f"mcp__claude-voice__{name}", {}, place=place, now=DAY).shape is False


@pytest.mark.parametrize(
    "tool", ["mcp__claude_ai_Gmail__send_message", "mcp__claude_ai_Google_Drive__share", "mcp__x__y"]
)
def test_a_connector_or_unknown_mcp_tool_is_no_shape(place, tool):
    assert routing.sort(tool, {}, place=place, now=DAY).shape is False


def test_without_knowing_the_sessions_folder_no_file_or_command_is_a_shape(place):
    assert routing.sort("Read", {"file_path": str(place.repo / "src/a.py")}, place=None, now=DAY).shape is False
    assert routing.sort("Bash", {"command": "git status"}, place=None, now=DAY).shape is False


# -- the evasion table: every one goes to the owner ---------------------------------------


@pytest.mark.parametrize(
    "command",
    [
        "g''it status",
        '"git" status',
        "gi\\t status",
        "/usr/bin/git status",
        "command git status",
        "env git status",
        "exec git status",
        "nice git status",
        "nohup git status",
        "timeout 5 git status",
        "sudo git status",
        "cat $HOME/x",
        "cat ${X}",
        "cat ~root/x",
        "cat ~/x",
        "git push git@github.com:owner-user/repo HEAD:feat/x",
        "git push origin feat/x:refs/heads/main",
        "git push -- origin main",
        "cat src/../../x",
        "cat /proc/self/cwd/x",
        "ls ｓrc",  # a fullwidth letter
        "gіt status",  # a Cyrillic i
        "cat $'\\x2e'",
        "ls `pwd`",
        "ls $(pwd)",
        "ls src > out",
        "ls src &",
    ],
)
def test_an_evasion_goes_to_the_owner_and_is_no_shape(place, command):
    out = bash(command, place)
    assert out.to == "roy" and out.shape is False, command


def test_a_symlink_out_of_the_repo_is_no_shape(place, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (place.repo / "link").symlink_to(outside)
    assert routing.sort("Read", {"file_path": str(place.repo / "link" / "x")}, place=place, now=DAY).shape is False
    assert bash("cat link/x", place).shape is False


def test_the_fixed_list_wins_inside_an_allowed_compound(place):
    out = bash("git status && git push origin HEAD:main", place)
    assert (out.line, out.shape) == (10, False)


# -- v2.5 to v2.9: the secret-path table and the read tools' option lists -------------------


@pytest.fixture
def home_secrets(place):
    """A home folder with every kind of secret file in it, and a token planted in each."""
    home = place.home
    files = [
        ".netrc",
        ".git-credentials",
        ".aws/credentials",
        ".docker/config.json",
        ".config/gh/hosts.yml",
        ".ssh/id_ed25519",
        ".gnupg/pubring.kbx",
        ".local/share/keyrings/login.keyring",
        ".config/someapp/api_token",
        ".config/claude-voice/env",
    ]
    for name in files:
        (home / name).parent.mkdir(parents=True, exist_ok=True)
        (home / name).write_text("ghp_planted\n")
    return files


@pytest.mark.parametrize(
    "name",
    [
        ".netrc",
        ".git-credentials",
        ".aws/credentials",
        ".docker/config.json",
        ".config/gh/hosts.yml",
        ".ssh/id_ed25519",
        ".gnupg/pubring.kbx",
        ".local/share/keyrings/login.keyring",
        ".config/someapp/api_token",
        ".config/claude-voice/env",
    ],
)
def test_line_1_every_secret_path_goes_to_the_owner_for_any_tool(place, home_secrets, name):
    target = str(place.home / name)
    for tool in ("Read", "Edit"):
        assert routing.sort(tool, {"file_path": target}, place=place, now=DAY).line == 1, (tool, name)
    assert routing.sort("Grep", {"pattern": "x", "path": target}, place=place, now=DAY).line == 1
    # Forward slashes: a backslash makes a command unreadable (line 9), which also goes to the
    # owner but would not show the secret check at work on Windows.
    assert line_of(f"cat {Path(target).as_posix()}", place) == 1


@pytest.mark.parametrize("name", ["server.pem", "deploy.key", "id_rsa", ".env", ".env.local", "sub/.env"])
def test_line_1_key_files_and_env_files_are_secret_even_inside_the_repo(place, name):
    target = place.repo / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("SECRET=1\n")
    out = routing.sort("Read", {"file_path": str(target)}, place=place, now=DAY)
    assert (out.line, out.shape) == (1, False)


@pytest.mark.parametrize(
    "command", ["cat ~/.netrc", "grep -r ghp_ ~/.config", "sort ~/.aws/credentials", 'cat "$HOME/.netrc"']
)
def test_line_1_a_secret_named_through_the_home_folder_goes_to_the_owner(place, home_secrets, command):
    out = bash(command, place)
    assert out.to == "roy" and out.shape is False


def test_line_1_a_link_to_a_secret_goes_to_the_owner(place, home_secrets):
    (place.repo / "x").symlink_to(place.home / ".netrc")
    for command in ("cat ./x", "head ./x", "grep pat ./x"):
        assert line_of(command, place) == 1, command
    assert routing.sort("Read", {"file_path": str(place.repo / "x")}, place=place, now=DAY).line == 1


@pytest.mark.parametrize("name", [".env.example", ".env.sample", ".env.template"])
def test_a_tracked_env_template_is_not_secret(place, home_secrets, name):
    """Only as a regular file git tracks in the repo: untracked or a link, it is secret."""
    target = str(place.repo / name)
    (place.repo / name).write_text("SECRET=\n")
    tracked = routing.sort("Read", {"file_path": target}, place=place, now=DAY, tracked=lambda path: True)
    assert (tracked.line, tracked.shape) == (None, True)
    untracked = routing.sort("Read", {"file_path": target}, place=place, now=DAY, tracked=lambda path: False)
    assert untracked.line == 1
    (place.repo / name).unlink()
    (place.repo / name).symlink_to(place.home / ".netrc")
    linked = routing.sort("Read", {"file_path": target}, place=place, now=DAY, tracked=lambda path: True)
    assert linked.line == 1


@pytest.mark.parametrize(
    "command",
    [
        "ls -la src",
        "ls -1 src",
        "cat -n src/a.py",
        "head -n 5 src/a.py",
        "head -n5 src/a.py",
        "tail -c 10 src/a.py",
        "wc -lw src/a.py",
        "grep -rn x src",
        "grep -rA3 x src",
        "grep -e x -e y src/a.py",
        "grep --include=a.py -r x src",
        "rg -n x src",
        "rg -g foo -t py x src",
        "rg --files src",
        "find src -name a.py -type f -maxdepth 2",
        "find src -print0",
        "sort -n -k 2 src/a.py",
        "jq -r --arg a b . src/a.py",
        "diff -u src/a.py src/b.py",
        "grep -n x -- src/a.py",
        "cat src/a.py src/b.py",
        "grep -r x",
    ],
)
def test_a_read_tool_with_only_listed_options_is_a_shape(place, command):
    (place.repo / "src" / "b.py").write_text("y = 2\n")
    out = bash(command, place)
    assert (out.shape, out.line) == (True, None), command


@pytest.mark.parametrize(
    "command",
    [
        "grep -f x src",
        "grep --file=x src",
        "sort --files0-from=x",
        "diff --from-file=x src/a.py",
        "find -files0-from x",
        "find src -newer x",
        "jq --slurpfile a x . src/a.py",
        "rg --pre=sh x src",
        "tail -f src/a.py",
        "tail -F src/a.py",
        "ls --color=always src",
        "grep -rf x src",
        "head -5 src/a.py",
        "grep -m",
        "grep -R x src",
        "grep --dereference-recursive x src",
        "rg -L x src",
        "rg --follow x src",
        "find -L src",
        "find src -follow",
        "diff -r src src",
        "cat -",
        "sort -o out src/a.py",
    ],
)
def test_a_read_tool_with_an_unlisted_option_goes_to_the_owner(place, command):
    assert bash(command, place).shape is False, command


def test_a_read_outside_the_areas_goes_to_the_owner(place, tmp_path):
    (tmp_path / "other-repo").mkdir()
    for command in ("ls ../other-repo", "find / -name x", "grep -r x /etc"):
        assert bash(command, place).shape is False, command


def test_diff_takes_two_regular_files_only(place, home_secrets):
    (place.repo / "a").mkdir()
    (place.repo / "b").mkdir()
    (place.repo / "a" / "x").symlink_to(place.home / ".netrc")
    (place.repo / "b" / "x").write_text("")
    assert bash("diff a b", place).shape is False
    assert bash("diff a/ b/x", place).shape is False
    assert bash("diff src/a.py", place).shape is False


def test_grep_r_does_not_follow_a_planted_link_while_grep_R_would(place, home_secrets):
    """Why -r is on the list and -R is not: -r reads no link it finds while recursing."""
    import shutil
    import subprocess

    if shutil.which("grep") is None:
        pytest.skip("no grep here")
    (place.repo / "x").symlink_to(place.home / ".netrc")
    plain = subprocess.run(["grep", "-r", "ghp_planted", "."], cwd=place.repo, capture_output=True, text=True)
    follows = subprocess.run(["grep", "-R", "ghp_planted", "."], cwd=place.repo, capture_output=True, text=True)
    assert "ghp_planted" not in plain.stdout and "ghp_planted" in follows.stdout
    assert bash("grep -r ghp_planted .", place).shape is True
    assert bash("grep -R ghp_planted .", place).shape is False


# -- where the sorting is recorded --------------------------------------------------------


def test_the_hook_sends_the_sessions_folder_with_an_approval():
    from claude_voice import phone_hook

    hook_input = {
        "hook_event_name": "PermissionRequest",
        "session_id": "s1",
        "tool_name": "Bash",
        "tool_input": {"command": "git status"},
        "cwd": "/work/repo",
    }
    assert phone_hook._prompt(hook_input, "")["cwd"] == "/work/repo"


async def test_an_opened_approval_records_how_it_was_sorted_and_still_goes_to_the_owner(tmp_path, place):
    import httpx
    from fakes import FakeClaude

    from claude_voice.oauth import OAuthProvider
    from claude_voice.server import build_app, build_server
    from claude_voice.sessions import SessionManager
    from claude_voice.store import Store

    secret = "s3cret-token-for-the-routing-tests"
    bearer = {"Authorization": f"Bearer {secret}"}
    (tmp_path / "home").mkdir(exist_ok=True)
    store = Store(tmp_path / "b.db")
    oauth = OAuthProvider(store, secret, "http://127.0.0.1:8811", static_token=secret)
    manager = SessionManager(store, project_root=tmp_path / "home", client_factory=FakeClaude())
    app = build_app(build_server(manager, oauth=oauth, conversations=lambda **k: [], live_dir=tmp_path / "live"))
    body = {"session_id": "s1", "kind": "approval", "tool": "Bash", "cwd": str(place.repo)}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8811") as c:
        await c.post("/api/v1/prompts", json={**body, "input": {"command": "git push -f origin x"}}, headers=bearer)
        await c.post("/api/v1/prompts", json={**body, "input": {"command": "git status"}}, headers=bearer)
        for _ in range(100):  # the sorting lands a moment after the prompt opens
            listed = (await c.get("/api/v1/prompts", headers=bearer)).json()["prompts"]
            if all("routed" in p for p in listed):
                break
            await asyncio.sleep(0.02)
    routed = [p["routed"] for p in listed]
    assert routed == [
        {"to": "roy", "line": 2, "lines": [2], "shape": False},
        {"to": "roy", "line": None, "lines": [], "shape": True},
    ]


def test_the_secret_files_are_the_same_as_the_shipped_deny_table():
    import json as json_

    from claude_voice import permissions

    shipped = json_.loads(Path(permissions.__file__).with_name("permissions.json").read_text(encoding="utf-8"))
    assert list(routing.SECRET_FILES) == shipped["secrets"]["files"]


# -- v2.10: a recursive read walks its tree first --------------------------------------------


@pytest.mark.parametrize("command", ["grep -r x .", "grep -rn x src", "rg x", "find . -name a.py", "ls -R"])
def test_a_recursive_read_over_a_secret_goes_to_the_owner_and_names_it(place, command):
    (place.repo / "src" / "deep").mkdir(parents=True, exist_ok=True)
    (place.repo / "src" / "deep" / ".env").write_text("SECRET=1\n")
    out = bash(command, place)
    assert out.shape is False, command
    assert any(".env" in why for why in out.why), out.why


def test_a_recursive_read_over_a_tracked_env_template_goes_through(place):
    (place.repo / "src" / ".env.example").write_text("SECRET=\n")
    out = routing.sort("Bash", {"command": "grep -r x src"}, place=place, now=DAY, tracked=lambda path: True)
    assert out.shape is True


def test_rg_with_no_path_walks_the_folder_it_runs_in(place):
    (place.repo / ".env").write_text("SECRET=1\n")
    assert bash("rg x", place).shape is False
    assert bash("cat src/a.py", place).shape is True


def test_a_recursive_read_over_a_tree_past_the_bound_goes_to_the_owner(place, monkeypatch):
    monkeypatch.setattr(routing, "WALK_ENTRIES", 3)
    for n in range(5):
        (place.repo / "src" / f"f{n}.py").write_text("")
    out = bash("grep -r x src", place)
    assert out.shape is False and any("too large" in why for why in out.why)


def test_the_walk_skips_git_and_never_follows_a_link(place, tmp_path):
    (place.repo / ".git").mkdir(exist_ok=True)
    (place.repo / ".git" / ".env").write_text("")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / ".env").write_text("SECRET=1\n")
    (place.repo / "src" / "link").symlink_to(outside)
    assert bash("grep -r x .", place).shape is True


def test_the_grep_tool_on_a_folder_walks_it_first(place):
    (place.repo / "src" / ".env").write_text("SECRET=1\n")
    out = routing.sort("Grep", {"pattern": "x", "path": str(place.repo / "src")}, place=place, now=DAY)
    assert out.shape is False and any(".env" in why for why in out.why)
    bare = routing.sort("Grep", {"pattern": "x"}, place=place, now=DAY)
    assert bare.shape is False


def test_a_hard_link_to_a_secret_goes_to_the_owner(place, home_secrets):
    os.link(place.home / ".netrc", place.repo / "notes.txt")
    assert routing.sort("Read", {"file_path": str(place.repo / "notes.txt")}, place=place, now=DAY).line == 1
    assert bash("cat notes.txt", place).shape is False
    assert bash("grep -r x .", place).shape is False


def test_a_hard_link_between_plain_files_is_not_secret(place, home_secrets):
    os.link(place.repo / "src" / "a.py", place.repo / "src" / "same.py")
    assert bash("cat src/same.py", place).shape is True


def test_a_home_folder_with_backslashes_is_read_as_it_is(tmp_path):
    home = tmp_path / "C:\\Users\\gh"
    (home / ".config").mkdir(parents=True)
    (home / ".netrc").write_text("")
    repo = tmp_path / "repo"
    repo.mkdir()
    place = routing.Place(repo=repo, scratchpad=None, home=home, owner="acme")
    assert routing.sort("Bash", {"command": 'cat "$HOME/.netrc"'}, place=place, now=DAY).line == 1


def test_the_secret_inodes_are_found_quickly_in_a_large_home(place, home_secrets):
    import time

    crowded = place.home / ".config" / "big"
    for n in range(500):
        sub = crowded / f"d{n}"
        sub.mkdir(parents=True)
        for m in range(100):
            (sub / f"f{m}").write_text("")
    routing.SECRET_INODES.clear()
    os.link(place.repo / "src" / "a.py", place.repo / "src" / "same.py")
    started = time.monotonic()
    out = bash("cat src/same.py", place)
    assert time.monotonic() - started < 1.0 and out.shape is True


def test_a_home_too_large_to_search_for_hard_links_sends_a_linked_file_to_the_owner(place, home_secrets, monkeypatch):
    monkeypatch.setattr(routing, "WALK_ENTRIES", 2)
    routing.SECRET_INODES.clear()
    os.link(place.repo / "src" / "a.py", place.repo / "src" / "same.py")
    out = bash("cat src/same.py", place)
    assert out.shape is False and any("hard link" in why for why in out.why)


def test_files_in_a_virtual_environment_skip_the_hard_link_search(place, home_secrets, monkeypatch):
    """Only when the search ran past its bound: a known set is always looked in."""
    venv = place.repo / ".venv" / "lib"
    venv.mkdir(parents=True)
    os.link(place.repo / "src" / "a.py", venv / "a.py")
    monkeypatch.setattr(routing, "_secret_inodes", lambda ask: None)
    assert routing.is_secret(".venv/lib/a.py", routing.Ask("Read", {}, place, DAY, [], [], [])) is False
    assert routing.is_secret("src/a.py", routing.Ask("Read", {}, place, DAY, [], [], [])) is True


@pytest.mark.parametrize("folder", [".git", ".venv", "node_modules"])
def test_a_hard_link_to_a_secret_in_a_linked_folder_is_still_secret(place, home_secrets, folder):
    routing.SECRET_INODES.clear()
    (place.repo / folder).mkdir(exist_ok=True)
    os.link(place.home / ".netrc", place.repo / folder / "n")
    assert routing.is_secret(f"{folder}/n", routing.Ask("Read", {}, place, DAY, [], [], [])) is True


async def test_sorting_a_prompt_never_holds_up_the_bridge(tmp_path):
    import asyncio
    import threading

    import httpx
    from fakes import FakeClaude

    from claude_voice.oauth import OAuthProvider
    from claude_voice.server import build_app, build_server
    from claude_voice.sessions import SessionManager
    from claude_voice.store import Store

    release = threading.Event()

    def slow_route(tool, given, cwd):
        release.wait(5)
        return {"to": "roy", "line": None, "lines": [], "shape": False}

    secret = "s3cret-token-for-the-routing-tests"
    bearer = {"Authorization": f"Bearer {secret}"}
    (tmp_path / "home").mkdir()
    store = Store(tmp_path / "b.db")
    oauth = OAuthProvider(store, secret, "http://127.0.0.1:8811", static_token=secret)
    manager = SessionManager(store, project_root=tmp_path / "home", client_factory=FakeClaude())
    app = build_app(
        build_server(manager, oauth=oauth, conversations=lambda **k: [], live_dir=tmp_path / "live", route=slow_route)
    )
    body = {"session_id": "s1", "kind": "approval", "tool": "Bash", "input": {"command": "ls"}, "cwd": str(tmp_path)}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8811") as c:
        made = await asyncio.wait_for(c.post("/api/v1/prompts", json=body, headers=bearer), 2)
        listed = (await asyncio.wait_for(c.get("/api/v1/prompts", headers=bearer), 2)).json()["prompts"]
        assert made.status_code == 200 and "routed" not in listed[0]
        release.set()
        for _ in range(50):
            listed = (await c.get("/api/v1/prompts", headers=bearer)).json()["prompts"]
            if "routed" in listed[0]:
                break
            await asyncio.sleep(0.05)
    assert listed[0]["routed"]["to"] == "roy"
