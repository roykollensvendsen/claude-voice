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
from dataclasses import dataclass
from typing import Any

from claude_voice import safe_git

#: Where one command ends and the next begins: the shell's joiners, a new line, a lone `&`, and
#: the marks of a subshell or a command substitution. They are kept in the split, so the guard
#: knows what joins each command to the next.
PARTS = re.compile(r"(&&|\|\||;|\||\n|&|\$\(|[()`])")
#: A redirection with its target, taken out before the line is split, so the `&` in `2>&1` or
#: `&> log` never splits a command and `2>` is never read as a remote: `N>&M`, `>&-`, `&> file`,
#: `>& file`, `N> file`, `>> file` and `>| file`. The target is a plain word only: bash runs a
#: command substitution in a target, so any other redirection stays in the text and fails the shape.
REDIRECTION = re.compile(
    r"\d*>&(?:\d+|-)|&>>?\s*[A-Za-z0-9._/+-]+|\d*>&\s*[A-Za-z0-9._/+-]+|\d*>>?\|?\s*[A-Za-z0-9._/+-]+"
)
#: A word that is still a redirection, such as one left by quoting, is never a remote.
REDIRECTION_WORD = re.compile(r"\d*[<>&]")
#: Marks that put a second command, a variable, a pattern or a subshell beside a push. The plain
#: shape has none of them, apart from one `&&` after a leading `cd`.
UNSAFE = re.compile(r"[;|&$`()<>\n\r*?\[\]{}\\!#]")
#: Options after `push` that keep the plain shape; any other option fails it.
PUSH_OPTIONS = frozenset({"-u", "--set-upstream", "-q", "--quiet", "-v", "--dry-run", "--force-with-lease"})
#: `--force-with-lease=<ref>:<sha>`, the one form of a lease with a value that keeps the shape.
LEASE = re.compile(r"--force-with-lease=[^\s:=]+:[0-9a-f]{4,40}")
#: Git variables in the hook's environment that never change where a push goes. Claude Code sets
#: GIT_EDITOR for every session; any other GIT_ variable fails the plain shape.
HARMLESS_GIT = frozenset({"GIT_EDITOR", "GIT_SEQUENCE_EDITOR", "GIT_PAGER", "GIT_TERMINAL_PROMPT"})
#: What the guard tells a session whose push is not in the plain shape.
SHAPE = (
    "`git push <remote> <branch>`, `git -C <folder> push <remote> <branch>` or "
    "`cd <folder> && git push <remote> <branch>`, as a call of its own"
)
#: Shells that run a command line given to them with -c.
SHELLS = frozenset({"sh", "bash", "dash", "zsh", "ksh"})
OWN_CLONE = "own-clone"
ASSIGNMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=")
#: The ways of writing a GitHub address, each leaving `owner/name` after it.
GITHUB = re.compile(r"(?:git@github\.com:|ssh://git@github\.com/|https://github\.com/)(.+)")
#: Options of `env` that take a word after them.
ENV_VALUES = frozenset({"-u", "--unset", "-C", "--chdir", "-S", "--split-string"})
TURN_ON = f'push_mode = "{OWN_CLONE}"'
#: Options before git's subcommand that take the next word as their value.
GIT_VALUES = ("-C", "-c", "--config-env", "--git-dir", "--work-tree")
#: The shell's own quoting, `$'...'` with escapes and `$"..."`, which bash turns into plain text.
ANSI_C = re.compile(r"\$'((?:[^'\\]|\\.)*)'", re.DOTALL)
ESCAPE = re.compile(r"\\(x[0-9A-Fa-f]{1,2}|u[0-9A-Fa-f]{1,4}|U[0-9A-Fa-f]{1,8}|[0-7]{1,3}|c.|.)", re.DOTALL)
SIMPLE_ESCAPES = {"a": "\a", "b": "\b", "e": "\x1b", "E": "\x1b", "f": "\f", "n": "\n", "r": "\r", "t": "\t", "v": "\v"}


@dataclass
class Push:
    """One `git push` in a command line: the words after `push`, or none for a push behind a
    wrapper, which the guard does not read word by word."""

    args: list[str]


#: How the guard reads one folder: a remote's push addresses by name, and the remote a bare
#: `git push` goes to.
Reader = tuple[Callable[[str], "str | None"], Callable[[], str]]
#: What a git alias runs, by its name and the folder git runs in (None for the session's own), or
#: None when there is no such alias.
Alias = Callable[[str, "str | None"], "str | None"]


def _escape(match: re.Match[str]) -> str:
    """The character one escape in `$'...'` stands for."""
    code = match.group(1)
    if code[0] in "xuU" and len(code) > 1:
        number = int(code[1:], 16)
        return chr(number) if number <= sys.maxunicode else ""
    if code[0] in "01234567":
        return chr(int(code, 8) & 0xFF)
    if code[0] == "c" and len(code) > 1:
        return chr(ord(code[1]) & 0x1F)
    return SIMPLE_ESCAPES.get(code, code)


def _unquoted(command: str) -> str:
    """The command with the shell's own quoting turned into plain quoting, as bash reads it."""
    plain = ANSI_C.sub(lambda match: shlex.quote(ESCAPE.sub(_escape, match.group(1))), command)
    return plain.replace('$"', '"')


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


def _join(base: str | None, folder: str) -> str:
    """Where `git -C folder` lands from `base`, None being the session's own folder."""
    if base is None or folder.startswith(("/", "~")):
        return folder
    return posixpath.join(base, folder)


def _literal(folder: str) -> bool:
    """Whether a folder after `cd` or -C is written out as it is: no option, no `~user`. Under
    CDPATH a relative folder may be found elsewhere, so only `./` and `../` keep it literal."""
    if (
        not folder
        or folder.startswith("-")
        or (folder.startswith("~") and not folder.startswith(("~/",)) and folder != "~")
    ):
        return False
    relative = not folder.startswith(("/", "~", "./", "../")) and folder not in (".", "..")
    # RULE: a relative cd under cdpath is never followed
    if relative and "CDPATH" in os.environ:
        return False
    return True


def _shape(command: str) -> tuple[bool, str | None]:
    """Whether the command is a plain push the guard can judge, and the folder it runs in: None
    for the session's own folder. The plain shape is `git push <words>` or
    `git -C <folder> push <words>`, after at most one `cd <folder> &&`, on one line, with nothing
    else in it: not even a redirection."""
    # RULE: a git variable in the hook environment fails the plain shape
    if any(name.startswith("GIT_") and name not in HARMLESS_GIT for name in os.environ):
        return False, None
    # The raw command, so a redirection, whose target bash may run as a command, fails the shape.
    text = command.strip(" \t")
    head, joiner, tail = text.partition("&&")
    lines = [head, tail] if joiner else [head]
    # RULE: a second command never rides on a plain push
    if any(UNSAFE.search(line) for line in lines):
        return False, None
    try:
        words = [shlex.split(line) for line in lines]
    except ValueError:
        return False, None
    folder = None
    if joiner:
        if len(words[0]) != 2 or words[0][0] != "cd" or not _literal(words[0][1]):
            return False, None
        # RULE: a push is judged by the remotes of the folder it runs in
        folder = words[0][1]
    rest = words[-1][1:] if words[-1][:1] == ["git"] else []
    if rest[:1] == ["-C"]:
        if len(rest) < 2 or not _literal(rest[1]):
            return False, None
        folder, rest = _join(folder, rest[1]), rest[2:]
    if rest[:1] != ["push"]:
        return False, None
    for word in rest[1:]:
        # RULE: a push option outside the allowed few fails the plain shape
        if word.startswith("-") and word not in PUSH_OPTIONS and not LEASE.fullmatch(word):
            return False, None
        if word.startswith((":", "+", "~")):
            return False, None
    return True, folder


def _pushes(
    command: str, alias: Alias | None = None, base: str | None = None, seen: frozenset[str] = frozenset()
) -> list[Push]:
    """Every `git push` in a command line, `git -C dir push` and `NAME=value git push` included.
    A push behind any other wrapper or a git alias is found too, with no words, as one the guard
    could not read word by word. `alias` reads what an alias runs; `base` is the folder the line
    starts in."""
    found = []
    # RULE: a push spelled in the shells own quoting is found
    for segment in PARTS.split(REDIRECTION.sub(" ", _unquoted(command)))[::2]:
        try:
            words = shlex.split(segment)
        except ValueError:
            words = segment.split()
        words, _ = _without_environment(words)
        if len(words) == 2 and words[0] == "cd":
            base = _join(base, words[1])
        elif not words or not _is_git(words[0]):
            # RULE: a push behind a wrapper is never let through unjudged
            if _hidden(words):
                found.append(Push([]))
        else:
            push = _git_push(words[1:], alias, base, seen)
            if push is not None:
                found.append(push)
    return found


def _git_options(rest: list[str], folder: str | None) -> tuple[list[str], str | None, dict[str, str]]:
    """The words from git's subcommand on, the folder git runs in, and the aliases the line sets
    with -c or --config-env. An alias whose value the line does not show is taken as a push."""
    inline = {}
    while rest and rest[0].startswith("-"):
        option, joined, value = rest[0].partition("=")
        if not joined:
            value = rest[1] if option in GIT_VALUES and len(rest) > 1 else ""
        if option in ("-C", "--git-dir"):
            folder = _join(folder, value)
        if option in ("-c", "--config-env") and value.lower().startswith("alias."):
            name, has_value, setting = value[len("alias.") :].partition("=")
            inline[name.lower()] = setting if option == "-c" and has_value else "push"
        rest = rest[2:] if not joined and option in GIT_VALUES else rest[1:]
    return rest, folder, inline


def _git_push(words: list[str], alias: Alias | None, base: str | None, seen: frozenset[str]) -> Push | None:
    """The push one `git` command runs, with the words after `push`, or None when it runs none.
    A push through an alias comes back with no words, so it is never in the plain shape."""
    rest, folder, inline = _git_options(words, base)
    if not rest:
        return None
    if rest[0] == "push":
        return Push(rest[1:])
    name = rest[0].lower()

    def lookup(key: str, where: str | None) -> str | None:
        if key.lower() in inline:
            return inline[key.lower()]
        return alias(key, where) if alias is not None else None

    runs = None if name in seen else lookup(rest[0], folder)
    if runs is None:
        return None
    line = runs[1:] if runs.startswith("!") else f"git {runs}"
    # RULE: a git alias is judged by what it runs
    return Push([]) if _pushes(line, lookup, folder, seen | {name}) else None


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


def _fork_only(push: Push, upstream: str, remote_url: Callable[[str], str | None], default: str) -> str | None:
    """Why a push in fork-only mode is refused, or None when it may go. Any repository of the
    upstream's owner is refused, not only the upstream itself."""
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
    own = None
    positional = _positional(push.args)
    if positional and positional[0] != "origin":
        own = f"the target {positional[0]} instead of origin"
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
    alias: Alias | None = None,
) -> tuple[str, str] | None:
    """("deny" or "ask", why) for a command with a push in it, or None when it may run.
    `in_folder` reads another folder's remotes, or answers None when it cannot; `alias` reads
    what a git alias runs."""
    pushes = _pushes(command, alias)
    if not pushes:
        return None
    plain, folder = _shape(command)
    # RULE: only a push in the plain shape is judged by its remotes
    if not plain:
        if own_clone:
            return ("ask", f"This push is not in a plain shape the guard can judge, such as {SHAPE}.")
        return (
            "deny",
            f"This push is refused: the guard can tell whether a push reaches {upstream}, not your own "
            f"fork, only in a plain shape. Run {SHAPE}.",
        )
    push = pushes[0]
    reader: Reader | None = (remote_url, default_remote)
    if folder is not None:
        reader = in_folder(folder) if in_folder is not None else None
    if reader is None:
        if own_clone:
            return ("ask", f"This push runs in another folder, {folder}, whose remotes the guard could not read.")
        return (
            "deny",
            f"Pushing from another folder, {folder}, is refused: the guard could not read whether it "
            f"pushes to {upstream}. Check that the folder is a clone, then push again.",
        )
    if own_clone:
        return _own_clone(push, upstream, reader[0], folder)
    reason = _fork_only(push, upstream, reader[0], reader[1]())
    return ("deny", reason) if reason else None


def refusal(
    command: str,
    upstream: str,
    remote_url: Callable[[str], str | None],
    default_remote: Callable[[], str],
) -> str | None:
    """Why `command` must not run in fork-only mode, or None when it may."""
    verdict = decide(command, upstream, False, remote_url, default_remote)
    return verdict[1] if verdict else None


def _git_at(path: str) -> Callable[..., str | None]:
    """Git in the clone at `path`, through the bridge's safe git: its output, or None."""

    def git(*args: str) -> str | None:
        try:
            out = safe_git.run("-C", path, *args, capture_output=True, text=True, timeout=10, check=True)
        except (OSError, subprocess.SubprocessError):
            return None
        return out.stdout.strip() or None

    return git


def _reader(path: str) -> Reader:
    """How to read the remotes of the clone at `path`, through the bridge's safe git."""
    git = _git_at(path)

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

    def alias(name: str, folder: str | None) -> str | None:
        path = cwd if folder is None else os.path.join(cwd, os.path.expanduser(folder))
        return _git_at(path)("config", "--get", f"alias.{name}")

    remote_url, default_remote = _reader(cwd)
    verdict = decide(command, upstream, own_clone, remote_url, default_remote, in_folder, alias)
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
