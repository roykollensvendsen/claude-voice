"""A guard on every `git push` from a session started for a role.

Claude Code runs it before every shell command of such a session (a PreToolUse
hook). It has two modes, and the role's entry in the owner's bridge settings
chooses between them; nothing in the clone, the environment or the command can:

- **Fork-only**, the default. A push that reaches the role's upstream
  repository, by remote name, by address or by a setting that redirects it, is
  refused; a push to the user's own fork goes through.
- **Own-clone**, when the role says `push_mode = "own-clone"`. The clone's push
  address must be the upstream repository the role names, or the push is
  refused. Every push still asks the owner, until the check that a branch is the
  session's own ships.

Run as ``python -m claude_voice.guard --upstream owner/name [--push-mode own-clone]``
with Claude Code's hook input on stdin.
"""

from __future__ import annotations

import argparse
import json
import re
import shlex
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from claude_voice import safe_git

SEGMENTS = re.compile(r"&&|\|\||;|\|")
OWN_CLONE = "own-clone"
ASSIGNMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=")
#: The ways of writing a GitHub address, each leaving `owner/name` after it.
GITHUB = re.compile(r"(?:git@github\.com:|ssh://git@github\.com/|https://github\.com/)(.+)")
#: Options of `env` that take a word after them.
ENV_VALUES = frozenset({"-u", "--unset", "-C", "--chdir", "-S", "--split-string"})
TURN_ON = f'push_mode = "{OWN_CLONE}"'


@dataclass
class Push:
    """One `git push` in a command line: the words after `push`, the options given to git
    before it, and whether anything was set in the environment for it."""

    args: list[str]
    before: list[str]
    environment: bool


def _without_environment(words: list[str]) -> tuple[list[str], bool]:
    """The command once leading `NAME=value` words and an `env` wrapper are taken off, and
    whether there were any."""
    rest, found = list(words), False
    while rest and ASSIGNMENT.match(rest[0]):
        rest, found = rest[1:], True
    if rest and rest[0] == "env":
        rest, found = rest[1:], True
        while rest and (rest[0].startswith("-") or ASSIGNMENT.match(rest[0])):
            rest = rest[2:] if rest[0] in ENV_VALUES else rest[1:]
    return rest, found


def _pushes(command: str) -> list[Push]:
    """Every `git push` in a command line, `git -C dir push` and `NAME=value git push` included."""
    found = []
    for segment in SEGMENTS.split(command):
        try:
            words = shlex.split(segment)
        except ValueError:
            words = segment.split()
        words, environment = _without_environment(words)
        if not words or words[0] != "git":
            continue
        rest, before = words[1:], list[str]()
        while rest and rest[0].startswith("-"):  # options before the subcommand, such as -C dir
            step = 2 if rest[0] in ("-C", "-c", "--config-env", "--git-dir", "--work-tree") else 1
            before, rest = before + rest[:step], rest[step:]
        if rest and rest[0] == "push":
            found.append(Push(rest[1:], before, environment))
    return found


def same_repository(url: str, repository: str) -> bool:
    """Whether a push address is the GitHub repository `owner/name`: the owner compared as
    GitHub does, without case, the name exactly, and the whole path, never a part of it."""
    # RULE: a push address is the same repository only when its whole GitHub path matches
    match = GITHUB.fullmatch(url.strip())
    if not match:
        return False
    path = match.group(1).removesuffix("/").removesuffix(".git")
    owner, _, name = repository.partition("/")
    return path.count("/") == 1 and path.split("/")[0].lower() == owner.lower() and path.split("/")[1] == name


def _owner_of(url: str) -> str | None:
    """The owner a GitHub push address names, without case, or None for any other address."""
    match = GITHUB.fullmatch(url.strip())
    return match.group(1).split("/")[0].lower() if match else None


def _elsewhere(push: Push) -> bool:
    """Whether the push runs in another clone than the session's, by -C, --git-dir or --work-tree."""
    return any(word.startswith(("-C", "--git-dir", "--work-tree")) for word in push.before)


def _sets_own_settings(push: Push) -> str | None:
    """What in the push gives git settings of its own, or None."""
    if push.environment:
        return "settings given to git through the environment"
    if any(word.startswith(("-c", "--config-env")) for word in push.before):
        return "settings given to git with -c or --config-env"
    if any(arg == "--repo" or arg.startswith("--repo=") for arg in push.args):
        return "a --repo option"
    return None


def _fork_only(push: Push, upstream: str, remote_url: Callable[[str], str | None], default: str) -> str | None:
    """Why a push in fork-only mode is refused, or None when it may go."""
    own = _sets_own_settings(push)
    # RULE: a fork-only push that gives git settings of its own is refused
    if own:
        return f"Pushing with {own} is refused: a contribute session pushes only to your own fork, as its remote says."
    # RULE: a fork-only push from another clone is refused
    if _elsewhere(push):
        return (
            "Pushing from another folder with -C, --git-dir or --work-tree is refused: the guard can only "
            f"tell that the session's own folder does not push to {upstream}. Change to that clone first."
        )
    positional = [a for a in push.args if not a.startswith("-")]
    target = positional[0] if positional else default
    # RULE: a fork-only push to an address written in the command is refused
    if "/" in target or ":" in target:
        return (
            f"Pushing to the address {target} is refused: name the remote instead, such as `fork`, "
            "so the guard can read where it really goes."
        )
    urls = remote_url(target)
    # RULE: a push whose address cannot be read is refused
    if urls is None:
        return (
            f"Pushing to {target} is refused: the guard could not read where it goes. "
            "Check the remote with `git remote -v` and the clone's settings with `git config --list --local`."
        )
    owner = upstream.partition("/")[0].lower()
    reached = [url for url in urls.splitlines() if _owner_of(url) == owner]
    # RULE: a contribute session never pushes to the upstream repository
    if reached:
        url = reached[0]
        return (
            f"Pushing to {upstream} ({url}) is refused: a contribute session pushes only to your own fork. "
            "Fork it with `gh repo fork --remote --remote-name fork`, push there, "
            f"then `gh pr create --repo {upstream}`. "
            f"For a role that works on the owner's own repository, the owner sets {TURN_ON} in its role entry."
        )
    return None


def _own_clone(push: Push, upstream: str, remote_url: Callable[[str], str | None]) -> tuple[str, str]:
    """A push in own-clone mode: refused when the clone does not push to the role's repository,
    and otherwise asked."""
    origin = remote_url("origin")
    pushes_to = origin.splitlines() if origin else []
    # RULE: in own-clone mode a clone that does not push to the role's repository is refused
    if not pushes_to or not all(same_repository(url, upstream) for url in pushes_to):
        return (
            "deny",
            f"Pushing is refused: this role's settings name {upstream}, "
            f"but the clone's origin pushes to {origin or 'an address the guard could not read'}. "
            "Check it with `git remote get-url --push origin`.",
        )
    own = _sets_own_settings(push)
    positional = [a for a in push.args if not a.startswith("-")]
    if own is None and positional and positional[0] != "origin":
        own = f"the target {positional[0]} instead of origin"
    why = (
        f"This push names {own}, so it needs your yes."
        if own
        else "Pushes ask you until the guard can tell this session's own branches."
    )
    # RULE: in own-clone mode every push asks the owner
    return ("ask", why)


def decide(
    command: str,
    upstream: str,
    own_clone: bool,
    remote_url: Callable[[str], str | None],
    default_remote: Callable[[], str],
) -> tuple[str, str] | None:
    """("deny" or "ask", why) for a command with a push in it, or None when it may run."""
    verdicts = []
    for push in _pushes(command):
        if own_clone:
            verdicts.append(_own_clone(push, upstream, remote_url))
        elif (reason := _fork_only(push, upstream, remote_url, default_remote())) is not None:
            verdicts.append(("deny", reason))
    denied = [v for v in verdicts if v[0] == "deny"]
    first = denied or verdicts
    return first[0] if first else None


def refusal(
    command: str,
    upstream: str,
    remote_url: Callable[[str], str | None],
    default_remote: Callable[[], str],
) -> str | None:
    """Why `command` must not run in fork-only mode, or None when it may."""
    verdict = decide(command, upstream, False, remote_url, default_remote)
    return verdict[1] if verdict else None


def answer(hook_input: dict[str, Any], upstream: str, own_clone: bool = False) -> dict[str, Any]:
    """Claude Code's hook answer: a refusal, a question, or nothing so the command runs."""
    if hook_input.get("tool_name") != "Bash":
        return {}
    command = str((hook_input.get("tool_input") or {}).get("command", ""))
    cwd = str(hook_input.get("cwd") or ".")

    def git(*args: str) -> str | None:
        try:
            out = safe_git.run("-C", cwd, *args, capture_output=True, text=True, timeout=10, check=True)
        except (OSError, subprocess.SubprocessError):
            return None
        return out.stdout.strip() or None

    def default_remote() -> str:
        pushed = git("rev-parse", "--abbrev-ref", "@{push}")
        return pushed.split("/", 1)[0] if pushed else "origin"

    verdict = decide(
        command, upstream, own_clone, lambda name: git("remote", "get-url", "--push", "--all", name), default_remote
    )
    if verdict is None:
        return {}
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": verdict[0],
            "permissionDecisionReason": verdict[1],
        }
    }


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m claude_voice.guard")
    parser.add_argument("--upstream", required=True)
    parser.add_argument("--push-mode", default="fork-only")
    options = parser.parse_args()
    try:
        hook_input = json.load(sys.stdin)
    except ValueError:
        return 0
    out = answer(hook_input, options.upstream, own_clone=options.push_mode == OWN_CLONE)
    if out:
        sys.stdout.write(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
