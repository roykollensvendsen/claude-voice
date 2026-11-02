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
from typing import Any

SEGMENTS = re.compile(r"&&|\|\||;|\|")


def _pushes(command: str) -> list[list[str]]:
    """The argument lists of every `git push` in a command line, `git -C dir push` included."""
    found = []
    for segment in SEGMENTS.split(command):
        try:
            words = shlex.split(segment)
        except ValueError:
            words = segment.split()
        if not words or words[0] != "git":
            continue
        rest = words[1:]
        while rest and rest[0].startswith("-"):  # options before the subcommand, such as -C dir
            rest = rest[2:] if rest[0] in ("-C", "-c") else rest[1:]
        if rest and rest[0] == "push":
            found.append(rest[1:])
    return found


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
    for args in _pushes(command):
        positional = [a for a in args if not a.startswith("-")]
        target = positional[0] if positional else default_remote()
        url = target if ("/" in target or ":" in target) else remote_url(target)
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
            out = subprocess.run(["git", "-C", cwd, *args], capture_output=True, text=True, timeout=10, check=True)
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
