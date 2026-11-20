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
