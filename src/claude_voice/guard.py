"""A guard that keeps a contribute session from pushing to the upstream repository.

Claude Code runs it before every shell command of a session started for the
contribute role (a PreToolUse hook). A `git push` whose target is the upstream
repository, by remote name or by address, is refused; a push to the user's own
fork goes through. Run as ``python -m claude_voice.guard --upstream owner/name``
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
from dataclasses import dataclass, field
from typing import Any

from claude_voice import safe_git

SEGMENTS = re.compile(r"&&|\|\||;|\|")


#: Push options whose value is the next word, so it is never read as the target.
VALUED = {"-o", "--push-option", "--receive-pack", "--exec", "--repo"}


@dataclass
class _Push:
    args: list[str]  # the words after `push`
    settings: list[str] = field(default_factory=list)  # keys set on the line itself, by -c or --config-env
    wrapped: bool = False  # something other than git itself starts the command


#: Commands that start the command after them, so `env git push` is still a push.
STARTERS = {
    "env", "command", "exec", "builtin", "nice", "nohup", "time", "timeout", "sudo", "doas", "xargs",
    "stdbuf", "ionice", "setsid", "chrt", "taskset", "unbuffer", "script", "flock", "watch",
}  # fmt: skip
#: Shells that run a string as a command.
SHELLS = {"sh", "bash", "zsh", "dash", "ksh", "fish", "eval"}


def _is_git(word: str) -> bool:
    return word == "git" or word.endswith("/git")


def _starts_git(words: list[str]) -> bool:
    """Whether the command's first word starts what follows: a setting such as A=1, or a starter."""
    first = words[0]
    return "=" in first.split("/")[0] or first.rsplit("/", 1)[-1] in STARTERS


def _shell_push(words: list[str]) -> bool:
    """A shell given a string that holds a git push."""
    return bool(words) and words[0].rsplit("/", 1)[-1] in SHELLS and any("git" in w and "push" in w for w in words[1:])


def _pushes(command: str) -> list[_Push]:
    """Every `git push` in a command line, `git -C dir push` included, with the settings the line
    gives git itself, and whether anything but git starts it."""
    found = []
    for segment in SEGMENTS.split(command):
        try:
            words = shlex.split(segment)
        except ValueError:
            words = segment.split()
        if _shell_push(words):
            found.append(_Push([], wrapped=True))
            continue
        at = next((i for i, word in enumerate(words) if _is_git(word)), None)
        if at is None or (at > 0 and not _starts_git(words)):
            continue
        rest, settings = words[at + 1 :], []
        while rest and rest[0].startswith("-"):  # options before the subcommand, such as -C dir
            if rest[0] == "-c" and len(rest) > 1:
                settings.append(rest[1].partition("=")[0])
            elif rest[0].startswith("--config-env="):
                settings.append(rest[0].removeprefix("--config-env=").partition("=")[0])
            rest = rest[2:] if rest[0] in ("-C", "-c") else rest[1:]
        if rest and rest[0] == "push":
            found.append(_Push(rest[1:], settings, wrapped=at != 0 or words[0] != "git"))
    return found


def _target(args: list[str], default_remote: Callable[[], str]) -> str:
    """Where a push goes: its --repo, else its first word that is no option or option value."""
    positional, at = [], 0
    while at < len(args):
        word = args[at]
        # RULE: a push names its target by repo too
        if word.startswith("--repo="):
            return word.removeprefix("--repo=")
        if word == "--repo" and at + 1 < len(args):
            return args[at + 1]
        # RULE: an option value is not read as the target
        if word in VALUED:
            at += 2
            continue
        if not word.startswith("-"):
            positional.append(word)
        at += 1
    return positional[0] if positional else default_remote()


def _names_upstream(url: str | None, upstream: str) -> bool:
    if not url:
        return False
    tail = re.sub(r"\.git/?$", "", url.strip()).lower()
    return tail.endswith("/" + upstream.lower()) or tail.endswith(":" + upstream.lower())


def refusal(
    command: str,
    upstream: str,
    remote_url: Callable[[str], str | None],
    default_remote: Callable[[], str],
) -> str | None:
    """Why `command` must not run, or None when it may."""
    for push in _pushes(command):
        # RULE: a push not started by git itself is refused
        if push.wrapped:
            return "A push is refused unless the command starts with `git` itself: run `git push ...` on its own."
        # RULE: a push that sets where it goes on its own line is refused
        if any(key.lower().startswith(("remote.", "url.")) for key in push.settings):
            return "A push is refused when its own line sets a remote or url setting: push without that -c."
        target = _target(push.args, default_remote)
        url = target if ("/" in target or ":" in target) else remote_url(target)
        # RULE: a push whose address cannot be read is refused
        if url is None:
            return (
                f"Pushing to {target} is refused: the guard could not read where it goes. "
                "Check the remote with `git remote -v` and the clone's settings with `git config --list --local`."
            )
        # RULE: a contribute session never pushes to the upstream repository
        if _names_upstream(url, upstream):
            return (
                f"Pushing to {upstream} is refused: a contribute session pushes only to your own fork. "
                "Fork it with `gh repo fork --remote --remote-name fork`, push there, "
                f"then `gh pr create --repo {upstream}`."
            )
    return None


def answer(hook_input: dict[str, Any], upstream: str) -> dict[str, Any]:
    """Claude Code's hook answer: a refusal, or nothing so the command runs."""
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

    reason = refusal(command, upstream, lambda name: git("remote", "get-url", "--push", name), default_remote)
    if reason is None:
        return {}
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m claude_voice.guard")
    parser.add_argument("--upstream", required=True)
    upstream = parser.parse_args().upstream
    try:
        hook_input = json.load(sys.stdin)
    except ValueError:
        return 0
    out = answer(hook_input, upstream)
    if out:
        sys.stdout.write(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
