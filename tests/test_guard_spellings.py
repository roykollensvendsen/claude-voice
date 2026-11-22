"""A push spelled with the shell's own quoting, or run through a git alias, is found as a push.

Many of these cases come from #380 by the contribute session. Main's guard has since been rebuilt
around one plain shape, so only these two gaps are carried over here. A push the guard finds but
cannot read plainly is refused in fork-only mode and asks the owner in own-clone mode.
"""

from __future__ import annotations

import subprocess

import pytest

from claude_voice import guard

UPSTREAM = "roykollensvendsen/claude-voice"
FORK = "https://github.com/someone/claude-voice"
OWNERS = f"https://github.com/{UPSTREAM}"


def decide(command, own_clone=False, aliases=None):
    urls = {"origin": OWNERS if own_clone else FORK}
    known = {name.lower(): runs for name, runs in (aliases or {}).items()}
    return guard.decide(
        command,
        UPSTREAM,
        own_clone,
        urls.get,
        lambda: "origin",
        alias=lambda name, folder: known.get(name.lower()),
    )


def caught(command, aliases=None):
    """Refused in fork-only mode, and asked in own-clone mode."""
    fork_only, own_clone = decide(command, False, aliases), decide(command, True, aliases)
    return fork_only is not None and fork_only[0] == "deny" and own_clone is not None and own_clone[0] == "ask"


# -- the shell's own quoting ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "command",
    [
        "$'git' push origin feat/x",
        "$'\\x67it' push origin feat/x",
        "$'\\147it' push origin feat/x",
        "$'\\u0067it' push origin feat/x",
        'g$"i"t push origin feat/x',
        "git $'push' origin feat/x",
        "git $'p\\x75sh' origin feat/x",
        "cd . && $'git' push origin feat/x",
    ],
)
def test_a_push_spelled_in_the_shells_own_quoting_is_found(command):
    assert caught(command)


@pytest.mark.parametrize("command", ["g''it push origin feat/x", '"g"it push origin feat/x'])
def test_a_push_spelled_in_plain_quotes_is_judged_as_the_plain_push(command):
    for own_clone in (False, True):
        assert decide(command, own_clone) == decide("git push origin feat/x", own_clone)


@pytest.mark.parametrize(
    "command", ["printf $'a\\tb\\n'", 'echo $"hello"', "git log --format=$'%h\\t%s'", "echo $'\\x'"]
)
def test_quoting_with_no_push_in_it_is_left_alone(command):
    assert decide(command) is None


@pytest.mark.parametrize(
    "quoted, plain", [("$'\\x67'", "g"), ("$'\\x'", "\\x"), ("$'\\q'", "\\q"), ("$'\\''", "'"), ("$'\\\\'", "\\")]
)
def test_the_shells_own_quoting_is_read_as_bash_reads_it(quoted, plain):
    assert guard._segments(f"echo {quoted}") == [["echo", plain]]


def test_a_plain_push_to_the_fork_still_goes_through():
    assert decide("git push origin feat/x") is None


# -- git aliases ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "command, aliases",
    [
        ("git p origin feat/x", {"p": "push"}),
        ("git P origin feat/x", {"p": "push"}),
        ("git p feat/x", {"p": "push origin"}),
        ("git q origin feat/x", {"q": "p", "p": "push"}),
        ("git s", {"s": "!git push origin feat/x"}),
        ("git s", {"s": "!sh -c 'git push origin feat/x'"}),
        ("git s", {"s": "-c remote.origin.url=x push"}),
        ("git p", {"p": "q", "q": "!git push origin feat/x"}),
        ("git -C . p origin feat/x", {"p": "push"}),
    ],
)
def test_a_git_alias_is_judged_by_what_it_runs(command, aliases):
    assert caught(command, aliases)


@pytest.mark.parametrize(
    "command, aliases",
    [
        ("git zz origin feat/x", {}),
        ("git send-pack origin feat/x", {}),
        ("git p", {"p": "q", "q": "p"}),
        ("git -c alias.p=push p origin feat/x", {}),
        ("git -c alias.st=status st", {"st": "status"}),
        ("git -c include.path=FILE zz origin feat/x", {}),
        ("git -c includeIf.onbranch:x.path=FILE zz origin feat/x", {}),
        ("git --config-env alias.p=WHAT p origin feat/x", {}),
        ("git --config-env=include.path=WHAT zz origin feat/x", {}),
        ("git -c alias.p=push p origin feat/x", {"p": "status"}),
        ("GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=alias.p GIT_CONFIG_VALUE_0=push git p origin feat/x", {"p": "status"}),
        ("GIT_CONFIG_PARAMETERS=\"'alias.p'='push'\" git p origin feat/x", {}),
        ("GIT_CONFIG_GLOBAL=FILE git p origin feat/x", {"p": "status"}),
        ("HOME=/elsewhere git p origin feat/x", {"p": "status"}),
        ("env XDG_CONFIG_HOME=/elsewhere git p origin feat/x", {"p": "status"}),
        ("git config alias.p push && git p origin feat/x", {"p": "status"}),
        ("true; git p origin feat/x", {"p": "status"}),
    ],
)
def test_a_subcommand_the_guard_cannot_place_is_taken_as_a_push(command, aliases):
    assert caught(command, aliases)


@pytest.mark.parametrize(
    "command, aliases",
    [
        ("git st", {"st": "status -sb"}),
        ("cd . && git st", {"st": "status -sb"}),
        ("git lfs pull", {}),
        ("git s", {"s": "!echo push"}),
        ("git -c core.pager=cat log", {}),
        ("GIT_CONFIG_GLOBAL=FILE git status", {}),
        ("git config alias.p push && git status", {}),
    ],
)
def test_an_alias_or_command_that_runs_no_push_is_left_alone(command, aliases):
    assert decide(command, False, aliases) is None


def test_the_hook_reads_the_aliases_of_the_folder_git_runs_in(tmp_path):
    def git(*args):
        subprocess.run(["git", "-C", str(tmp_path / "clone"), *args], check=True, capture_output=True)

    (tmp_path / "clone").mkdir()
    git("init", "-q")
    git("remote", "add", "origin", FORK)
    git("config", "alias.p", "push")

    def deny(command, cwd):
        hook = {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(cwd)}
        return guard.answer(hook, UPSTREAM).get("hookSpecificOutput", {}).get("permissionDecision")

    assert deny("git p origin feat/x", tmp_path / "clone") == "deny"
    assert deny("git -C clone p origin feat/x", tmp_path) == "deny"
    assert deny("cd clone && git p origin feat/x", tmp_path) == "deny"
    assert deny("git status", tmp_path / "clone") is None
