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
import os
import posixpath
import re
import shlex
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from claude_voice import safe_git

#: Where one command ends and the next begins: the shell's joiners, a new line, a lone `&`, and
#: the marks of a subshell or a command substitution. They are kept in the split, so the guard
#: knows what joins each command to the next.
PARTS = re.compile(r"(&&|\|\||;|\||\n|&|\$\(|[()`])")
#: Joiners after which a `cd` still holds for the next command. After `|` or `&`, or inside a
#: subshell, it may not, so a push there is judged in both folders.
LASTING = frozenset({"&&", "||", ";", "\n"})
#: A folder the guard cannot read, such as `cd $DIR` or `cd -`.
UNREAD = "?"
#: A redirection with its target, taken out before the line is split, so the `&` in `2>&1` or
#: `&> log` never splits a command and `2>` is never read as a remote: `N>&M`, `>&-`, `&> file`,
#: `>& file`, `N> file`, `>> file` and `>| file`.
REDIRECTION = re.compile(r"\d*>&(?:\d+|-)|&>>?\s*[^\s;&|()<>]+|\d*>&\s*[^\s;&|()<>]+|\d*>>?\|?\s*[^\s;&|()<>]+")
#: A word that is still a redirection, such as one left by quoting, is never a remote.
REDIRECTION_WORD = re.compile(r"\d*[<>&]")
#: Shells that run a command line given to them with -c.
SHELLS = frozenset({"sh", "bash", "dash", "zsh", "ksh"})
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
    placed: bool = True  # False for a push behind a wrapper the guard does not read word by word
    #: The folders the push may run in, after `cd` and `-C`: None is the session's own folder.
    folders: list[str | None] = field(default_factory=lambda: [None])


#: How the guard reads one folder: a remote's push addresses by name, and the remote a bare
#: `git push` goes to.
Reader = tuple[Callable[[str], "str | None"], Callable[[], str]]


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


def _positional(args: list[str]) -> list[str]:
    """The words after `push` that name a remote or a branch: not an option, not a redirection."""
    return [a for a in args if not a.startswith("-") and not REDIRECTION_WORD.match(a)]


def _is_git(word: str) -> bool:
    return word.rsplit("/", 1)[-1] == "git"


def _inner(words: list[str]) -> list[str]:
    """The command lines that a shell's -c or an eval among these words would run."""
    lines = []
    for i, word in enumerate(words):
        name = word.rsplit("/", 1)[-1]
        if name in SHELLS and "-c" in words[i + 1 :]:
            at = words.index("-c", i + 1)
            lines += words[at + 1 : at + 2]
        elif name == "eval":
            lines.append(" ".join(words[i + 1 :]))
    return lines


def _hidden(words: list[str]) -> bool:
    """Whether a push sits behind a wrapper, such as `timeout 60 git push` or `sh -c '...'`.
    Words that `echo` or `printf` print are text, never run."""
    if words and words[0] in ("echo", "printf"):
        return False
    if any(_is_git(word) and "push" in words[i + 1 :] for i, word in enumerate(words)):
        return True
    return any(_pushes(line) for line in _inner(words))


def _readable(folder: str) -> str:
    """A folder as written, or UNREAD when the shell would fill it in: a variable, `-`, a pattern."""
    return UNREAD if folder in ("", "-") or any(mark in folder for mark in "$`*?[{") else folder


def _join(base: str | None, folder: str) -> str:
    """Where `cd folder` or `git -C folder` lands from `base`, None being the session's own folder."""
    if UNREAD in (base, folder):
        return UNREAD
    if base is None or folder.startswith(("/", "~")):
        return folder
    return posixpath.join(base, folder)


def _cd(words: list[str]) -> str:
    """Where a `cd` goes: home with no folder, UNREAD for any option or anything the shell fills in."""
    if len(words) == 1:
        return "~"
    return _readable(words[1]) if len(words) == 2 and not words[1].startswith("-") else UNREAD


def _pushes(command: str) -> list[Push]:
    """Every `git push` in a command line, `git -C dir push` and `NAME=value git push` included,
    each with the folders it may run in. A push behind any other wrapper is found too, as one the
    guard could not read word by word."""
    found = []
    folders: list[str | None] = [None]
    depth, quoted = 0, False
    # RULE: a redirection is never read as a remote
    parts = PARTS.split(REDIRECTION.sub(" ", command))
    for at in range(0, len(parts), 2):
        segment, after = parts[at], (parts[at + 1] if at + 1 < len(parts) else "\n")
        try:
            words = shlex.split(segment)
        except ValueError:
            words = segment.split()
        words, environment = _without_environment(words)
        if words and words[0] == "cd":
            moved: list[str | None] = [_join(folder, _cd(words)) for folder in folders]
            # RULE: a push is judged by the remotes of the folder it runs in
            lasting = depth == 0 and not quoted and after in LASTING
            folders = moved if lasting else list(dict.fromkeys([*folders, *moved]))
        elif not words or not _is_git(words[0]):
            # RULE: a push behind a wrapper is never let through unjudged
            if _hidden(words):
                found.append(Push([], [], environment, placed=False, folders=list(folders)))
        else:
            rest, before = words[1:], list[str]()
            while rest and rest[0].startswith("-"):  # options before the subcommand, such as -C dir
                step = 2 if rest[0] in ("-C", "-c", "--config-env", "--git-dir", "--work-tree") else 1
                before, rest = before + rest[:step], rest[step:]
            if rest and rest[0] == "push":
                within = list(folders)
                for i, word in enumerate(before):
                    if word == "-C":
                        place = _readable(before[i + 1]) if i + 1 < len(before) else UNREAD
                        within = [_join(folder, place) for folder in within]
                found.append(Push(rest[1:], before, environment, folders=within))
        if after in ("(", "$("):
            depth += 1
        elif after == ")":
            depth = max(depth - 1, 0)
        elif after == "`":
            quoted = not quoted
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
    """Whether the push names its repository with --git-dir or --work-tree, which the guard does
    not follow. A folder given with `cd` or -C is followed, and its own remotes are read."""
    return any(word.startswith(("--git-dir", "--work-tree")) for word in push.before)


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
    """Why a push in fork-only mode is refused, or None when it may go. Any repository of the
    upstream's owner is refused, not only the upstream itself."""
    if not push.placed:
        return (
            "This command runs git push behind another command or a shell, so the guard cannot read "
            "where it goes. Run `git push <remote> <branch>` on its own line."
        )
    own = _sets_own_settings(push)
    # RULE: a fork-only push that gives git settings of its own is refused
    if own:
        return f"Pushing with {own} is refused: a contribute session pushes only to your own fork, as its remote says."
    # RULE: a fork-only push from another clone is refused
    if _elsewhere(push):
        return (
            "Pushing from another folder with --git-dir or --work-tree is refused: the guard can only "
            f"tell where a folder it can read pushes, not whether it reaches {upstream}. "
            "Run `cd <folder>` in a call of its own, then push."
        )
    positional = _positional(push.args)
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
    unread = [url for url in urls.splitlines() if _owner_of(url) is None]
    # RULE: a fork-only push to an address that is not plainly on GitHub is refused
    if unread or not urls.strip():
        return (
            f"Pushing to {target} ({unread[0] if unread else 'no address'}) is refused: the guard could not "
            "read where it goes. A contribute session pushes to a fork named by a plain GitHub address, "
            "such as git@github.com:<you>/<repo>.git or https://github.com/<you>/<repo>."
        )
    owner = upstream.partition("/")[0].lower()
    reached = [url for url in urls.splitlines() if _owner_of(url) == owner]
    # RULE: a contribute session never pushes to the upstream repository
    if reached:
        url = reached[0]
        return (
            f"Pushing to {url} is refused: it is a repository of {owner}, who owns {upstream}, and a "
            "contribute session pushes only to your own fork. "
            "Fork it with `gh repo fork --remote --remote-name fork`, push there, "
            f"then `gh pr create --repo {upstream}`. "
            f"For a role that works on the owner's own repository, the owner sets {TURN_ON} in its role entry."
        )
    return None


def _own_clone(
    push: Push, upstream: str, remote_url: Callable[[str], str | None], folder: str | None = None
) -> tuple[str, str]:
    """A push in own-clone mode: refused when the clone does not push to the role's repository,
    and otherwise asked. `folder` is where it runs, when that is not the session's own folder."""
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
    if not push.placed:
        own = "git push behind another command or a shell, which the guard cannot read word by word"
    positional = _positional(push.args)
    if own is None and positional and positional[0] != "origin":
        own = f"the target {positional[0]} instead of origin"
    if own is None and _elsewhere(push):
        own = "another folder with --git-dir or --work-tree, judged here by this folder's origin"
    if own is None and folder is not None:
        own = f"another folder, {folder}"
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
    in_folder: Callable[[str], Reader | None] | None = None,
) -> tuple[str, str] | None:
    """("deny" or "ask", why) for a command with a push in it, or None when it may run.
    `in_folder` reads another folder's remotes, or answers None when it cannot."""
    verdicts = []
    for push in _pushes(command):
        for folder in push.folders:
            if folder is None:
                reader: Reader | None = (remote_url, default_remote)
            else:
                reader = in_folder(folder) if in_folder is not None and folder != UNREAD else None
            if reader is None:
                where = "a folder the guard cannot name" if folder == UNREAD else folder
                verdicts.append(
                    ("ask", f"This push runs in another folder, {where}, whose remotes the guard could not read.")
                    if own_clone
                    else (
                        "deny",
                        f"Pushing from another folder, {where}, is refused: the guard could not read whether "
                        f"it pushes to {upstream}. Run `cd <folder>` in a call of its own, then push.",
                    )
                )
            elif own_clone:
                verdicts.append(_own_clone(push, upstream, reader[0], folder))
            elif (reason := _fork_only(push, upstream, reader[0], reader[1]())) is not None:
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


def _reader(path: str) -> Reader:
    """How to read the remotes of the clone at `path`, through the bridge's safe git."""

    def git(*args: str) -> str | None:
        try:
            out = safe_git.run("-C", path, *args, capture_output=True, text=True, timeout=10, check=True)
        except (OSError, subprocess.SubprocessError):
            return None
        return out.stdout.strip() or None

    def default_remote() -> str:
        pushed = git("rev-parse", "--abbrev-ref", "@{push}")
        return pushed.split("/", 1)[0] if pushed else "origin"

    return (lambda name: git("remote", "get-url", "--push", "--all", name)), default_remote


def answer(hook_input: dict[str, Any], upstream: str, own_clone: bool = False) -> dict[str, Any]:
    """Claude Code's hook answer: a refusal, a question, or nothing so the command runs."""
    if hook_input.get("tool_name") != "Bash":
        return {}
    command = str((hook_input.get("tool_input") or {}).get("command", ""))
    cwd = str(hook_input.get("cwd") or ".")

    def in_folder(folder: str) -> Reader | None:
        path = os.path.join(cwd, os.path.expanduser(folder))
        return _reader(path) if os.path.isdir(path) else None

    remote_url, default_remote = _reader(cwd)
    verdict = decide(command, upstream, own_clone, remote_url, default_remote, in_folder)
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
