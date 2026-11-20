"""The push guard's two modes (github-rights v1, R8 and f6 to f9).

A role pushes in fork-only mode unless the owner's own settings give it `push_mode = "own-clone"`.
In own-clone mode the clone's push address must be the role's written repository, and every push
still asks until the claim check ships. In fork-only mode a push that reaches the upstream
repository is refused, however the address is written or redirected.
"""

import subprocess

import pytest

from claude_voice import guard, roles

UPSTREAM = "roykollensvendsen/claude-voice"
OWNERS = f"https://github.com/{UPSTREAM}"
FORK = "git@github.com:someone/claude-voice.git"


def decide(command, urls, own_clone=False):
    return guard.decide(command, UPSTREAM, own_clone, remote_url=urls.get, default_remote=lambda: "origin")


def git(folder, *args):
    subprocess.run(["git", "-C", str(folder), *args], check=True, capture_output=True)


def clone(tmp_path, origin):
    tmp_path.mkdir(parents=True, exist_ok=True)
    git(tmp_path, "init", "-q")
    git(tmp_path, "remote", "add", "origin", origin)
    return tmp_path


def hook(folder, command):
    return {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(folder)}


def decision(out):
    return out["hookSpecificOutput"]["permissionDecision"], out["hookSpecificOutput"]["permissionDecisionReason"]


# -- which mode: only the owner's written field turns own-clone on (f9) -------------------------


def test_without_the_push_mode_field_a_push_to_the_owners_repo_is_refused_naming_the_field():
    verdict, reason = decide("git push origin feat/x", {"origin": OWNERS})
    assert verdict == "deny" and 'push_mode = "own-clone"' in reason


def test_with_the_push_mode_field_a_push_to_the_owners_own_repo_asks():
    verdict, reason = decide("git push origin feat/x", {"origin": OWNERS}, own_clone=True)
    assert verdict == "ask" and reason


@pytest.mark.parametrize(
    ("value", "flag"), [("own-clone", True), ("fork-only", False), ("Own-Clone", False), ("", False)]
)
def test_own_clone_mode_is_on_only_when_the_role_entry_names_it_exactly(value, flag):
    role = {"guard": True, "upstream": UPSTREAM, **({"push_mode": value} if value else {})}
    command = roles.guard_settings(role)
    assert command and ("--push-mode own-clone" in command) is flag


@pytest.mark.parametrize(
    "command",
    [
        "git push origin feat/x",
        "git -c claude-voice.push-mode=own-clone push origin feat/x",
        "git push --push-mode=own-clone origin feat/x",
        "CLAUDE_VOICE_PUSH_MODE=own-clone git push origin feat/x",
    ],
)
def test_a_clones_settings_the_environment_or_the_command_never_turn_own_clone_mode_on(tmp_path, monkeypatch, command):
    folder = clone(tmp_path, OWNERS)
    git(folder, "config", "claude-voice.push-mode", "own-clone")
    monkeypatch.setenv("CLAUDE_VOICE_PUSH_MODE", "own-clone")
    monkeypatch.setenv("PUSH_MODE", "own-clone")
    verdict, reason = decision(guard.answer(hook(folder, command), UPSTREAM))
    assert verdict == "deny" and "fork" in reason


# -- own-clone mode: the push address only confirms what the owner wrote (R8 b1) ----------------


def test_in_own_clone_mode_a_clone_that_does_not_push_to_the_role_s_repository_is_refused():
    verdict, reason = decide("git push origin feat/x", {"origin": FORK}, own_clone=True)
    assert verdict == "deny" and UPSTREAM in reason and FORK in reason


def test_in_own_clone_mode_an_unreadable_push_address_is_refused():
    verdict, _ = decide("git push origin feat/x", {}, own_clone=True)
    assert verdict == "deny"


@pytest.mark.parametrize(
    "command",
    [
        f"git push {OWNERS} feat/x",
        "git push upstream feat/x",
        "git push --repo=origin feat/x",
        "git -c remote.origin.pushurl=x push origin feat/x",
        "git --config-env=remote.origin.pushurl=X push origin feat/x",
        "GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=a GIT_CONFIG_VALUE_0=b git push origin feat/x",
    ],
)
def test_in_own_clone_mode_a_push_that_names_its_own_target_or_settings_asks(command):
    verdict, _ = decide(command, {"origin": OWNERS, "upstream": OWNERS}, own_clone=True)
    assert verdict == "ask"


def test_in_own_clone_mode_every_push_asks_the_owner(tmp_path):
    folder = clone(tmp_path, OWNERS)
    verdict, _ = decision(guard.answer(hook(folder, "git push origin feat/x"), UPSTREAM, own_clone=True))
    assert verdict == "ask"


# -- fork-only mode: every way of reaching the upstream repository is refused (R8 tests 1 to 4) ---


def test_a_fork_only_role_whose_origin_was_changed_to_the_owners_repo_is_refused(tmp_path):
    folder = clone(tmp_path, FORK)
    git(folder, "remote", "set-url", "origin", OWNERS)
    verdict, reason = decision(guard.answer(hook(folder, "git push origin feat/x"), UPSTREAM))
    assert verdict == "deny" and UPSTREAM in reason


def test_a_fork_only_role_pushing_to_the_owners_address_on_the_command_line_is_refused():
    verdict, _ = decide(f"git push {OWNERS}.git feat/x", {"origin": FORK})
    assert verdict == "deny"


@pytest.mark.parametrize(
    "command",
    [
        "git -c remote.origin.pushurl=https://github.com/roykollensvendsen/claude-voice push origin feat/x",
        "git -c url.x.pushInsteadOf=y push origin feat/x",
        "git --config-env=remote.origin.pushurl=X push origin feat/x",
        "git push --repo=https://github.com/roykollensvendsen/claude-voice feat/x",
    ],
)
def test_a_fork_only_push_that_gives_git_settings_of_its_own_is_refused(command):
    verdict, _ = decide(command, {"origin": FORK})
    assert verdict == "deny"


@pytest.mark.parametrize(
    "command",
    [
        "GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=remote.origin.pushurl GIT_CONFIG_VALUE_0=x git push origin feat/x",
        "GIT_CONFIG_PARAMETERS=\"'remote.origin.pushurl'='x'\" git push origin feat/x",
        "env GIT_CONFIG_COUNT=1 git push origin feat/x",
        "env -u HOME GIT_DIR=x git push origin feat/x",
    ],
)
def test_settings_given_to_git_through_the_environment_before_a_push_are_refused_in_fork_only_mode(command):
    verdict, _ = decide(command, {"origin": FORK})
    assert verdict == "deny"


def test_a_push_insteadof_in_the_clone_that_points_a_fork_at_the_owners_repo_is_refused(tmp_path):
    folder = clone(tmp_path, "https://github.com/someone/claude-voice")
    git(folder, "config", f"url.{OWNERS}.pushInsteadOf", "https://github.com/someone/claude-voice")
    verdict, _ = decision(guard.answer(hook(folder, "git push origin feat/x"), UPSTREAM))
    assert verdict == "deny"


@pytest.mark.parametrize(
    "command",
    ["git push gh:someone/claude-voice feat/x", "git push https://github.com/someone/claude-voice feat/x"],
)
def test_a_fork_only_push_to_an_address_written_in_the_command_is_refused(tmp_path, command):
    """git rewrites a written address through pushInsteadOf too, so only a remote's name is read."""
    folder = clone(tmp_path, FORK)
    git(folder, "config", f"url.{OWNERS}.pushInsteadOf", "gh:someone/claude-voice")
    verdict, reason = decision(guard.answer(hook(folder, command), UPSTREAM))
    assert verdict == "deny" and "name the remote" in reason


@pytest.mark.parametrize("option", ["-C {}", "--git-dir={}/.git", "--git-dir {}/.git --work-tree {}"])
def test_a_fork_only_push_from_another_clone_is_refused(tmp_path, option):
    """The session's own folder pushes `fork` to the fork; the other clone's `fork` is the upstream."""
    here, there = clone(tmp_path / "here", FORK), clone(tmp_path / "there", FORK)
    git(here, "remote", "add", "fork", FORK)
    git(there, "remote", "add", "fork", OWNERS)
    command = f"git {option.format(there, there)} push fork feat/x"
    verdict, _ = decision(guard.answer(hook(here, command), UPSTREAM))
    assert verdict == "deny"


@pytest.mark.parametrize(
    "command",
    [
        "git -C {there} push origin feat/x",
        "cd {there} && git push origin feat/x",
        "cd {there} && git push origin feat/x 2>&1",
    ],
)
def test_a_push_is_judged_by_the_remotes_of_the_folder_it_runs_in(tmp_path, command):
    """The session's folder pushes to the owner; the other clone is a fork, and goes."""
    here, there = clone(tmp_path / "here", OWNERS), clone(tmp_path / "there", FORK)
    assert guard.answer(hook(here, command.format(there=there)), UPSTREAM) == {}


@pytest.mark.parametrize("command", ["cd {there} && git push origin feat/x", "git -C {there} push origin feat/x"])
def test_a_push_after_moving_into_a_clone_of_the_upstream_is_refused(tmp_path, command):
    here, there = clone(tmp_path / "here", FORK), clone(tmp_path / "there", OWNERS)
    verdict, _ = decision(guard.answer(hook(here, command.format(there=there)), UPSTREAM))
    assert verdict == "deny"


@pytest.mark.parametrize(
    "command",
    [
        "(cd {there}); git push origin feat/x",
        "cd {there} & git push origin feat/x",
        "cd {there} | git push origin feat/x",
    ],
)
def test_a_move_that_may_not_last_judges_the_push_in_both_folders(tmp_path, command):
    """Here is the owner's repo; a move inside a subshell, or beside a pipe, does not reach the push."""
    here, there = clone(tmp_path / "here", OWNERS), clone(tmp_path / "there", FORK)
    verdict, _ = decision(guard.answer(hook(here, command.format(there=there)), UPSTREAM))
    assert verdict == "deny"


@pytest.mark.parametrize(
    "command",
    [
        "false && cd {there} || git push origin feat/x",
        "true || cd {there} && git push origin feat/x",
        "false && cd {there}; git push origin feat/x",
        "cd {there} || git push origin feat/x",
        "cd {there}; git push origin feat/x",
        "cd {there}\ngit push origin feat/x",
    ],
)
def test_a_cd_the_shell_may_skip_is_never_taken_as_certain(tmp_path, command):
    """Here is the owner's repo. Only a cd joined to the push by && alone surely runs first."""
    here, there = clone(tmp_path / "here", OWNERS), clone(tmp_path / "there", FORK)
    verdict, _ = decision(guard.answer(hook(here, command.format(there=there)), UPSTREAM))
    assert verdict == "deny"


@pytest.mark.parametrize(
    "command",
    [
        "if true; then cd THERE; fi; git push origin feat/x",
        "for d in 1; do cd THERE; done; git push origin feat/x",
        "{ cd THERE; }; git push origin feat/x",
        "case x in x) cd THERE;; esac; git push origin feat/x",
        "! cd THERE && git push origin feat/x",
        "builtin cd THERE && git push origin feat/x",
        "command cd THERE && git push origin feat/x",
        "pushd THERE && git push origin feat/x",
        "popd && git push origin feat/x",
        "eval cd THERE && git push origin feat/x",
        "eval 'cd THERE' && git push origin feat/x",
    ],
)
def test_a_folder_change_the_guard_cannot_follow_leaves_the_folder_unread(tmp_path, command):
    """Here is a fork; the move goes to the owner's repo in a shape the guard does not follow."""
    here, there = clone(tmp_path / "here", FORK), clone(tmp_path / "there", OWNERS)
    verdict, _ = decision(guard.answer(hook(here, command.replace("THERE", str(there))), UPSTREAM))
    assert verdict == "deny"


@pytest.mark.parametrize(
    "command",
    [
        "CDPATH={up} cd claude-voice && git push origin feat/x",
        "export CDPATH={up}; cd claude-voice && git push origin feat/x",
        "cd claude-voice && git push origin feat/x",
    ],
)
def test_a_relative_cd_under_cdpath_is_never_followed(tmp_path, monkeypatch, command):
    """A fork sits under here; CDPATH would send the same cd to a clone of the owner's repo."""
    here = clone(tmp_path / "here", FORK)
    clone(here / "claude-voice", FORK)
    up = tmp_path / "up"
    clone(up / "claude-voice", OWNERS)
    if "CDPATH" not in command:
        monkeypatch.setenv("CDPATH", str(up))
    verdict, _ = decision(guard.answer(hook(here, command.format(up=up)), UPSTREAM))
    assert verdict == "deny"


@pytest.mark.parametrize(
    "command",
    [
        "export GIT_DIR=THERE/.git; git push origin feat/x",
        "export GIT_WORK_TREE=THERE && git push origin feat/x",
        "declare -x GIT_DIR=THERE/.git; git push origin feat/x",
        "set -a; GIT_DIR=THERE/.git; git push origin feat/x",
        "export GIT_CONFIG_GLOBAL=THERE/cfg; git push origin feat/x",
        "ENV",
    ],
)
def test_git_settings_set_anywhere_in_the_command_or_the_hook_environment_are_refused(tmp_path, monkeypatch, command):
    """Here is a fork; the settings point git at a clone of the owner's repo."""
    here, there = clone(tmp_path / "here", FORK), clone(tmp_path / "there", OWNERS)
    if command == "ENV":
        monkeypatch.setenv("GIT_DIR", str(there / ".git"))
        command = "git push origin feat/x"
    verdict, _ = decision(guard.answer(hook(here, command.replace("THERE", str(there))), UPSTREAM))
    assert verdict == "deny"


@pytest.mark.parametrize(
    "command",
    [
        "export GIT_COMMON_DIR=/x/.git; git push origin feat/x",
        "export HOME=/tmp/h; git push origin feat/x",
        "HOME=/tmp/h; git push origin feat/x",
        "export XDG_CONFIG_HOME=/tmp/h; git push origin feat/x",
        "printf -v HOME /tmp/h; git push origin feat/x",
        ". ./settings && git push origin feat/x",
        "source ./settings && git push origin feat/x",
        "declare -x FOO=1; git push origin feat/x",
        "typeset -x FOO=1; git push origin feat/x",
        "readonly FOO=1; git push origin feat/x",
        "set -a; git push origin feat/x",
        "set -o allexport; git push origin feat/x",
        "env FOO=1 true; git push origin feat/x",
        "if true; then export FOO=1; fi; git push origin feat/x",
    ],
)
def test_a_push_in_a_command_that_sets_variables_brings_its_own_settings(command):
    """The fork's push would go, but the same command may change what the push reads."""
    assert decide(command, {"origin": FORK})[0] == "deny"
    assert decide(command, {"origin": OWNERS}, own_clone=True)[0] == "ask"


# -- the plain shape (R8 f13): only a lone push is judged by its remotes -------------------------

PLAIN = "git push origin feat/x"


@pytest.mark.parametrize(
    "command",
    [
        "git push origin feat/x",
        "git push -u origin feat/x",
        "git push --set-upstream --quiet origin feat/x",
        "git push -v --dry-run origin feat/x",
        "git push --force-with-lease origin feat/x",
        "git push --force-with-lease=feat/x:0123abc origin feat/x",
        "git push origin feat/x 2>&1",
        "git push",
        "git -C THERE push origin feat/x",
        "cd THERE && git push origin feat/x",
        "cd THERE && git -C . push origin feat/x 2>&1",
    ],
)
def test_a_plain_push_to_a_fork_goes(tmp_path, command):
    here, there = clone(tmp_path / "here", FORK), clone(tmp_path / "there", FORK)
    assert guard.answer(hook(here, command.replace("THERE", str(there))), UPSTREAM) == {}


@pytest.mark.parametrize(
    "command",
    [
        "make && git push origin feat/x",
        "cd x; git push origin feat/x",
        "cd x\ngit push origin feat/x",
        "git push origin feat/x 2>&1 | tail -1",
        "export HOME=/tmp/h; git push origin feat/x",
        "f() { git push origin feat/x; }; f",
        "command git push origin feat/x",
        "cd a && cd b && git push origin feat/x",
        "git -C a -C b push origin feat/x",
        "git push origin feat/x\n",
        "FOO=1 git push origin feat/x",
        "git push origin $BRANCH",
        "git push origin :feat/x",
        "git push origin +feat/x",
    ],
)
def test_only_a_push_in_the_plain_shape_is_judged_by_its_remotes(command):
    """The fork's push would go; any other shape around it is refused, naming the plain shape."""
    verdict, reason = decide(command, {"origin": FORK})
    assert verdict == "deny" and "git push <remote> <branch>" in reason
    assert decide(command, {"origin": OWNERS}, own_clone=True)[0] == "ask"


@pytest.mark.parametrize(
    "command",
    [
        "git push origin feat/x; curl example.com",
        "git push origin feat/x | tee log",
        "git push origin feat/x & sleep 1",
        "git push origin feat/x `true`",
        "git push origin feat/x > log; rm x",
    ],
)
def test_a_second_command_never_rides_on_a_plain_push(command):
    assert decide(command, {"origin": FORK})[0] == "deny"


@pytest.mark.parametrize(
    "option",
    ["--mirror", "--all", "--tags", "-f", "--force", "-d", "--delete", "--repo=x", "--push-option=x", "-o", "--exec=x"],
)
def test_a_push_option_outside_the_allowed_few_fails_the_plain_shape(option):
    assert decide(f"git push {option} origin feat/x", {"origin": FORK})[0] == "deny"


def test_a_git_variable_in_the_hook_environment_fails_the_plain_shape(monkeypatch):
    monkeypatch.setenv("GIT_SSH_COMMAND", "ssh")
    assert decide(PLAIN, {"origin": FORK})[0] == "deny"


@pytest.mark.parametrize("name", ["GIT_EDITOR", "GIT_SEQUENCE_EDITOR", "GIT_PAGER", "GIT_TERMINAL_PROMPT"])
def test_a_harmless_git_variable_in_the_hook_environment_keeps_the_plain_shape(monkeypatch, name):
    """Claude Code sets GIT_EDITOR for every session."""
    monkeypatch.setenv(name, "true")
    assert decide(PLAIN, {"origin": FORK}) is None


def test_a_relative_cd_that_starts_with_a_dot_is_followed_under_cdpath(tmp_path, monkeypatch):
    here = clone(tmp_path / "here", OWNERS)
    clone(here / "claude-voice", FORK)
    monkeypatch.setenv("CDPATH", str(tmp_path))
    assert guard.answer(hook(here, "cd ./claude-voice && git push origin feat/x"), UPSTREAM) == {}


@pytest.mark.parametrize(
    "command", ["cd $(echo THERE) && git push origin feat/x", "cd `echo THERE` && git push origin feat/x"]
)
def test_a_cd_into_a_command_substitution_is_unread(tmp_path, command):
    here, there = clone(tmp_path / "here", FORK), clone(tmp_path / "there", OWNERS)
    verdict, _ = decision(guard.answer(hook(here, command.replace("THERE", str(there))), UPSTREAM))
    assert verdict == "deny"


@pytest.mark.parametrize(
    "command",
    [
        "cd $OTHER && git push origin feat/x",
        "cd - && git push origin feat/x",
        "cd {there}/missing && git push origin feat/x",
        "git -C $OTHER push origin feat/x",
    ],
)
def test_a_push_in_a_folder_the_guard_cannot_read_is_refused(tmp_path, command):
    here, there = clone(tmp_path / "here", FORK), clone(tmp_path / "there", FORK)
    verdict, _ = decision(guard.answer(hook(here, command.format(there=there)), UPSTREAM))
    assert verdict == "deny"


def test_a_push_in_another_readable_clone_asks_in_own_clone_mode_naming_that_folder(tmp_path):
    here, there = clone(tmp_path / "here", OWNERS), clone(tmp_path / "there", OWNERS)
    verdict, reason = decision(guard.answer(hook(here, f"cd {there} && git push origin feat/x"), UPSTREAM, True))
    assert verdict == "ask" and str(there) in reason


def test_a_fork_remote_with_a_second_push_address_at_the_upstream_is_refused(tmp_path):
    folder = clone(tmp_path, FORK)
    git(folder, "remote", "add", "fork", FORK)
    git(folder, "config", "--add", "remote.fork.pushurl", FORK)
    git(folder, "config", "--add", "remote.fork.pushurl", OWNERS)
    verdict, _ = decision(guard.answer(hook(folder, "git push fork feat/x"), UPSTREAM))
    assert verdict == "deny"


@pytest.mark.parametrize("name", ["old-name", "claude-voice-ui", "Claude-Voice"])
def test_a_fork_only_push_to_any_repository_of_the_upstreams_owner_is_refused(name):
    """GitHub sends a renamed repository's old address on to the new one."""
    verdict, _ = decide("git push fork feat/x", {"fork": f"https://github.com/RoyKollensvendsen/{name}"})
    assert verdict == "deny"


@pytest.mark.parametrize(
    "url",
    [
        f"https://x-access-token@github.com/{UPSTREAM}.git",
        f"ssh://github.com/{UPSTREAM}",
        f"http://github.com/{UPSTREAM}",
        f"git@gh-alias:{UPSTREAM}.git",
        f"gh:{UPSTREAM}",
    ],
)
def test_a_fork_only_push_to_an_address_that_is_not_plainly_on_github_is_refused(url):
    verdict, reason = decide("git push fork feat/x", {"fork": url})
    assert verdict == "deny" and "could not read where it goes" in reason


HIDDEN = [
    "cd x\ngit push upstream main",
    "sleep 1 & git push upstream main",
    "(git push upstream main)",
    "echo $(git push upstream main)",
    "/usr/bin/git push upstream main",
    "sh -c 'git push upstream main'",
    "bash -c 'git push upstream main'",
    "eval git push upstream main",
    "timeout 60 git push upstream main",
    "nice -n 5 git push upstream main",
    "command git push upstream main",
]


@pytest.mark.parametrize("command", HIDDEN)
def test_a_push_behind_a_wrapper_is_never_let_through_unjudged(command):
    verdict, _ = decide(command, {"upstream": OWNERS, "origin": FORK})
    assert verdict == "deny"


@pytest.mark.parametrize("command", HIDDEN)
def test_a_push_in_any_shape_of_command_line_asks_in_own_clone_mode(command):
    verdict, _ = decide(command, {"upstream": OWNERS, "origin": OWNERS}, own_clone=True)
    assert verdict == "ask"


@pytest.mark.parametrize(
    "command",
    [
        "git push 2>&1",
        "git push &> log",
        "git push >& log",
        "git push 2> err.txt fork main",
        "git push fork main > /dev/null 2>&1",
    ],
)
def test_a_redirection_is_never_read_as_a_remote(command):
    assert decide(command, {"origin": FORK, "fork": FORK}) is None


def test_a_redirection_is_never_named_as_the_target_in_own_clone_mode():
    verdict, reason = decide("git push 2>&1 | tail -1", {"origin": OWNERS}, own_clone=True)
    assert verdict == "ask"
    assert "2>" not in reason


def test_a_lone_ampersand_still_splits_the_commands():
    verdict, _ = decide("make & git push upstream main", {"upstream": OWNERS, "origin": FORK})
    assert verdict == "deny"


def test_a_fork_only_push_from_another_clone_asks_in_own_clone_mode_naming_the_other_folder():
    verdict, reason = decide("git -C ../other push origin feat/x", {"origin": OWNERS}, own_clone=True)
    assert verdict == "ask" and "another folder" in reason


def test_in_own_clone_mode_a_second_push_address_elsewhere_is_refused():
    verdict, _ = decide("git push origin feat/x", {"origin": f"{OWNERS}\n{FORK}"}, own_clone=True)
    assert verdict == "deny"


def test_a_push_to_the_users_own_fork_is_still_let_through():
    assert decide("git push origin feat/x", {"origin": FORK}) is None


# -- comparing addresses (f6) -------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "git@github.com:roykollensvendsen/claude-voice",
        "git@github.com:roykollensvendsen/claude-voice.git",
        "ssh://git@github.com/roykollensvendsen/claude-voice.git",
        "https://github.com/roykollensvendsen/claude-voice",
        "https://github.com/roykollensvendsen/claude-voice.git",
        "https://github.com/roykollensvendsen/claude-voice/",
        "https://github.com/RoyKollensvendsen/claude-voice",
    ],
)
def test_every_spelling_of_a_github_address_is_compared_as_one(url):
    assert guard.same_repository(url, UPSTREAM)


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/roykollensvendsen-x/claude-voice",
        "https://github.com/roykollensvendsen/claude-voice-x",
        "https://github.com/x/roykollensvendsen/claude-voice",
        "https://github.com/roykollensvendsen/Claude-Voice",
        "https://gitlab.com/roykollensvendsen/claude-voice",
        "https://github.com.evil/roykollensvendsen/claude-voice",
        "/home/someone/claude-voice",
    ],
)
def test_a_push_address_is_the_same_repository_only_when_its_whole_github_path_matches(url):
    assert not guard.same_repository(url, UPSTREAM)


@pytest.mark.parametrize(
    "url",
    ["https://github.com/roykollensvendsen-x/claude-voice", "https://github.com/roykollensvendsen/claude-voice-x"],
)
def test_in_own_clone_mode_a_look_alike_push_address_is_refused(url):
    verdict, _ = decide("git push origin feat/x", {"origin": url}, own_clone=True)
    assert verdict == "deny"
