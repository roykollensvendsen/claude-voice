"""Running git from the bridge.

The bridge runs git in folders a session can write to: to sort a prompt, for the live view's
git status, for the work cards, in the session hook and in the updater. A repository's own
settings can name a program for git to start. So every git the bridge runs switches off the
settings it can, reads no system-wide settings file, and is skipped in a repository whose own
settings name a program anyway.

Two known limits, until sessions run as their own user. The check runs just before git does,
so a session that changes its settings in between is not caught. And only the repository's own
settings are checked: the owner's global ones (~/.gitconfig) are trusted, and the rules refuse
a session's edits to them.
"""

from __future__ import annotations

import os
import subprocess
from logging import getLogger
from typing import Any

log = getLogger(__name__)

#: How long reading a repository's own settings may take before git is skipped there.
SETTINGS_SECONDS = 5.0

#: The settings that let a repository name a program for git to start, switched off. Git never
#: steps into a submodule either, since a submodule's own settings aren't checked.
OFF = (
    "-c",
    "core.fsmonitor=false",
    "-c",
    f"core.hooksPath={os.devnull}",
    "-c",
    "submodule.recurse=false",
    "-c",
    "fetch.recurseSubmodules=false",
)

#: The subcommands measured against a repository that names a program for every setting
#: that can start one. With OFF and the check below, none of them starts anything. A new
#: subcommand is added here with a case in tests/test_safe_git.py, and with any switches it
#: needs: a diff, for example, needs --no-ext-diff and --no-textconv.
MEASURED = frozenset(
    {
        "ls-files",
        "branch",
        "config",
        "remote",
        "rev-parse",
        "rev-list",
        "status",
        "log",
        "show",
        "symbolic-ref",
        "fetch",
        "merge",
        "reset",
    }
)

#: How a setting that names a program ends. A key the list doesn't name still fails closed
#: when it ends the usual way: filter.<x>.process, merge.<x>.driver, credential.<url>.helper.
PROGRAM_ENDINGS = (
    "program",
    "command",
    "helper",
    "driver",
    "textconv",
    "process",
    "clean",
    "smudge",
    "external",
    "askpass",
    "gitproxy",
    "uploadpack",
    "receivepack",
    "editor",
    "pager",
    ".vcs",
)


def _leading(args: tuple[str, ...]) -> int:
    """Where the subcommand is in `args`, past `-C <folder>`, `-c <key=value>` and `--flags`."""
    at = 0
    while at < len(args) and args[at].startswith("-"):
        at += 2 if args[at] in ("-C", "-c") else 1
    return at


def _subcommand(args: tuple[str, ...]) -> str:
    at = _leading(args)
    return args[at] if at < len(args) else ""


def command(*args: str, git: str = "git") -> list[str]:
    """The git command line for `args`, with the switches in front."""
    # RULE: a git subcommand off the measured list is refused
    if _subcommand(args) not in MEASURED:
        raise ValueError(f"git {_subcommand(args)!r} was not measured; add it to safe_git.MEASURED with a test")
    # `git config` only reads settings here and starts nothing; with OFF in front it would
    # read back OFF's own values instead of the repository's.
    switches = () if _subcommand(args) == "config" else OFF
    at = _leading(args) + 1
    # A repository's own settings can tell status to look inside a submodule; only this flag wins.
    # RULE: git status never looks inside a submodule
    after = ("--ignore-submodules=all",) if _subcommand(args) == "status" else ()
    # RULE: the bridge runs git with fsmonitor and hooks off and no system config
    return [git, *switches, *args[:at], *after, *args[at:]]


def env() -> dict[str, str]:
    """The environment git runs in: the bridge's own, reading no system-wide settings file."""
    return {**os.environ, "GIT_CONFIG_NOSYSTEM": "1"}


def program_named(args: tuple[str, ...], git: str = "git", cwd: Any = None) -> str | None:
    """The first setting of the repository's own that names a program, or None."""
    words = [git, *args[: _leading(args)], "config", "--show-scope", "--includes", "--name-only", "--list"]
    try:
        # An include can name a pipe that never ends, so the read has a limit and no input.
        out = subprocess.run(
            words,
            env=env(),
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=SETTINGS_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        # RULE: a repo whose settings cannot be read in time is skipped
        return "its settings could not be read in time"
    for line in out.stdout.splitlines():
        scope, _, key = line.partition("\t")
        if scope in ("local", "worktree") and key.lower().endswith(PROGRAM_ENDINGS):
            return key
    return None


def run(*args: str, git: str = "git", **options: Any) -> subprocess.CompletedProcess[Any]:
    """Run git with the switches; `options` go to subprocess.run as they are. In a repository
    whose own settings name a program, git is skipped and the result is a failure."""
    words = command(*args, git=git)
    named = program_named(args, git, options.get("cwd"))
    # RULE: git runs nothing in a repo whose own settings name a program
    if named is not None:
        where = next((args[i + 1] for i in range(_leading(args)) if args[i] == "-C"), options.get("cwd") or ".")
        why = f"git {_subcommand(args)} skipped in {where}: its own settings name a program ({named})"
        log.warning(why)
        if options.get("check"):
            raise subprocess.CalledProcessError(1, words, "", why)
        empty: Any = "" if options.get("text") else b""
        return subprocess.CompletedProcess(words, 1, empty, why if options.get("text") else why.encode())
    return subprocess.run(words, env=env(), **options)  # noqa: PLW1510 - callers pass check
