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
#: The mode for a role that brings its own settings and no guard: only branch deletes are judged.
NO_DELETES = "no-deletes"
#: Options of a push that delete branches on the remote, without their dashes. Git takes any
#: start of a long option that names one option alone, so `--del` is `--delete`.
DELETING = ("delete", "prune", "mirror")
#: Settings that make a push delete with no delete word in it: a configured refspec or mirror,
#: given on the command line with -c or --config-env, or set earlier in the clone.
DELETING_SETTING = re.compile(r"remote\..+\.(push|mirror)|push\..+", re.IGNORECASE)
ASSIGNMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=")
#: The ways of writing a GitHub address, each leaving `owner/name` after it.
GITHUB = re.compile(r"(?:git@github\.com:|ssh://git@github\.com/|https://github\.com/)(.+)")
#: Options of `env` that take a word after them.
ENV_VALUES = frozenset({"-u", "--unset", "-C", "--chdir", "-S", "--split-string"})
TURN_ON = f'push_mode = "{OWN_CLONE}"'
#: Git's own subcommands that never send a branch anywhere, from `git --list-cmds=builtins` and
#: `--list-cmds=main` of git 2.55. Git never lets an alias stand in for one of them. Left out on
#: purpose: push, send-pack, http-push and subtree, which push; filter-branch, which runs shell
#: text; and for-each-repo and submodule--helper, which run a git command named on the line.
#: Any other subcommand is an alias, a known extension, or taken as a push.
GIT_COMMANDS = frozenset(
    """
    add am annotate apply archive backfill bisect blame branch bugreport bundle cat-file check-attr
    check-ignore check-mailmap checkout checkout-index checkout--worker check-ref-format cherry
    cherry-pick clean clone column commit commit-graph commit-tree config count-objects credential
    credential-cache credential-cache--daemon credential-store describe diagnose diff diff-files
    diff-index diff-pairs difftool diff-tree fast-export fast-import fetch fetch-pack fmt-merge-msg
    for-each-ref format-patch format-rev fsck fsck-objects fsmonitor--daemon gc
    get-tar-commit-id grep hash-object help history hook index-pack init init-db interpret-trailers
    last-modified log ls-files ls-remote ls-tree mailinfo mailsplit maintenance merge merge-base
    merge-file merge-index merge-ours merge-recursive merge-recursive-ours merge-recursive-theirs
    merge-subtree merge-tree mktag mktree multi-pack-index mv name-rev notes pack-objects
    pack-redundant pack-refs patch-id pickaxe prune prune-packed pull range-diff read-tree rebase
    receive-pack reflog refs remote remote-ext remote-fd repack replace replay repo rerere reset
    restore revert rev-list rev-parse rm shortlog show show-branch show-index show-ref
    sparse-checkout stage stash status stripspace switch symbolic-ref tag
    unpack-file unpack-objects update-index update-ref update-server-info upload-archive
    upload-archive--writer upload-pack url-parse var verify-commit verify-pack verify-tag version
    whatchanged worktree write-tree
    citool difftool--helper gui instaweb mergetool request-pull send-email submodule
    lfs
    """.split()
)
#: Git's own subcommands left off that list on purpose, so each is taken as a push. A test holds
#: the list and these together against `git --list-cmds=builtins` and `--list-cmds=main`, so a git
#: with a new subcommand turns the tests red. The last line is Git for Windows' own.
LEFT_OUT = frozenset(
    """
    push send-pack for-each-repo submodule--helper http-push subtree filter-branch
    archimport credential-libsecret cvsexportcommit cvsimport cvsserver daemon gui--askpass
    gui--askyesno http-backend http-fetch imap-send merge-octopus merge-one-file merge-resolve p4
    quiltimport remote-ftp remote-ftps remote-http remote-https shell sh-i18n--envsubst svn
    web--browse
    credential-wincred gui.tcl survey update
    """.split()
)
#: Forms of a listed subcommand that run a command or shell text named on the line, by the
#: subcommand: an option, or the word that chooses the action.
RUNS_A_COMMAND = {
    "rebase": ("-x", "--exec"),
    "bisect": ("run",),
    "submodule": ("foreach",),
    "difftool": ("-x", "--extcmd"),
}
#: The mode for a role with neither the guard nor its own settings: only merges are judged.
MERGES_ONLY = "merges-only"
#: gh's options that take the next word as their value, for gh itself and for `gh api`.
GH_VALUES = frozenset(
    "-R --repo --hostname -X --method -f --raw-field -F --field -H --header --input -q --jq "
    "-t --template --cache -p --preview".split()
)
#: `gh api` options that send fields, which makes the call a POST unless it names a method.
GH_FIELDS = frozenset({"-f", "--raw-field", "-F", "--field", "--input"})
GH_FIELDS_JOINED = ("-f", "-F", "--raw-field=", "--field=", "--input=")
#: API paths that merge a pull request or a branch, or set a commit status or a check run.
GH_PATH = re.compile(r"^/?repos/[^/]+/[^/]+/(?:statuses/|pulls/[^/]+/merge/?$|merges/?$|check-runs|check-suites)")
#: GraphQL mutations that merge, or set a status.
GH_MUTATIONS = re.compile(
    r"mergePullRequest|enablePullRequestAutoMerge|mergeBranch|createCommitStatus|createCheckRun|updateCheckRun"
)
#: Options before git's subcommand that take the next word as their value.
GIT_VALUES = ("-C", "-c", "--config-env", "--git-dir", "--work-tree")
#: The shell's own quoting, `$'...'` with escapes and `$"..."`, which bash turns into plain text.
ANSI_C = re.compile(r"\$'((?:[^'\\]|\\.)*)'", re.DOTALL)
ESCAPE = re.compile(r"\\(x[0-9A-Fa-f]{1,2}|u[0-9A-Fa-f]{1,4}|U[0-9A-Fa-f]{1,8}|[0-7]{1,3}|c.|.)", re.DOTALL)
SIMPLE_ESCAPES = {
    "a": "\a",
    "b": "\b",
    "e": "\x1b",
    "E": "\x1b",
    "f": "\f",
    "n": "\n",
    "r": "\r",
    "t": "\t",
    "v": "\v",
    "\\": "\\",
    "'": "'",
    '"': '"',
    "?": "?",
}


@dataclass
class Push:
    """One `git push` in a command line: the words after `push`, or none for a push behind a
    wrapper, which the guard does not read word by word."""

    args: list[str]
    #: The names of the settings given before `push` with -c or --config-env.
    settings: tuple[str, ...] = ()


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
    # bash keeps the backslash of an escape it does not know, such as `\x` with no digits
    return SIMPLE_ESCAPES.get(code, "\\" + code)


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


def _is_gh(word: str) -> bool:
    return word.rsplit("/", 1)[-1] == "gh"


def _gh_words(words: list[str]) -> list[str]:
    """The words after gh that name what it does, with every option and its value left out."""
    out, i = [], 0
    while i < len(words):
        word = words[i]
        if word in GH_VALUES:
            i += 2
            continue
        if not word.startswith("-"):
            out.append(word)
        i += 1
    return out


def _gh_method(words: list[str]) -> str:
    """The HTTP method a `gh api` call uses: the one it names, else POST when it sends fields."""
    method, fields = None, False
    for i, word in enumerate(words):
        if word in ("-X", "--method") and i + 1 < len(words):
            method = words[i + 1]
        elif word.startswith("--method="):
            method = word.split("=", 1)[1]
        elif word.startswith("-X"):
            method = word[2:]
        elif word in GH_FIELDS or word.startswith(GH_FIELDS_JOINED):
            fields = True
    return (method or ("POST" if fields else "GET")).upper()


def _gh_write(words: list[str]) -> str | None:
    """What a gh call, given the words after gh, does that only the manager may: None for anything else."""
    named = _gh_words(words)
    # RULE: the flags of gh never hide a merge
    if named[:2] == ["pr", "merge"]:
        return "merges a pull request"
    if named[:2] in (["alias", "set"], ["alias", "import"]):
        return "sets up a gh alias, which could stand for a merge"
    if named[:1] != ["api"] or len(named) < 2:
        return None
    # RULE: a writing api call to a merge or a status is refused
    if named[1] == "graphql":
        return "merges or sets a status through GitHub's API" if any(GH_MUTATIONS.search(w) for w in words) else None
    if GH_PATH.search(named[1]) and _gh_method(words) not in ("GET", "HEAD"):
        return "merges or sets a status through GitHub's API"
    return None


def merge_or_status(command: str) -> str | None:
    """What a command line does through gh that only a role with `merges` may, or None. A gh call
    behind a wrapper, `env`, or a shell's -c or eval is read too."""
    for raw in _segments(command):
        words, _ = _without_environment(raw)
        if words[:1] in (["echo"], ["printf"]):
            continue
        for i, word in enumerate(words):
            found = _gh_write(words[i + 1 :]) if _is_gh(word) else None
            if found:
                return found
        for line in _inner(words):
            found = merge_or_status(line)
            if found:
                return found
    return None


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
    # RULE: a git setting given in the environment on the line fails the plain shape
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


def _segments(command: str) -> list[list[str]]:
    """The words of each command in a line, with the shell's own quoting read as bash reads it."""
    out = []
    # RULE: a push spelled in the shells own quoting is found
    for segment in PARTS.split(REDIRECTION.sub(" ", _unquoted(command)))[::2]:
        try:
            out.append(shlex.split(segment))
        except ValueError:
            out.append(segment.split())
    return out


def _pushes(
    command: str, alias: Alias | None = None, base: str | None = None, seen: frozenset[str] = frozenset()
) -> list[Push]:
    """Every `git push` in a command line, `git -C dir push` and `NAME=value git push` included.
    A push behind any other wrapper or a git alias is found too, with no words, as one the guard
    could not read word by word. `alias` reads what an alias runs; `base` is the folder the line
    starts in."""
    found = []
    segments = _segments(command)
    # Another command on the line may set up an alias before git runs, so an alias is followed
    # only on a line with no other command but `cd`.
    alone = sum(1 for words in segments if words[:1] not in ([], ["cd"])) <= 1
    for raw in segments:
        words, set_here = _without_environment(raw)
        if len(words) == 2 and words[0] == "cd":
            base = _join(base, words[1])
        elif not words or not _is_git(words[0]):
            # RULE: a push behind a wrapper is never let through unjudged
            if _hidden(words):
                found.append(Push([]))
        else:
            push = _git_push(words[1:], alias if alone and not set_here else None, base, seen)
            if push is not None:
                found.append(push)
    return found


def _git_options(rest: list[str], folder: str | None) -> tuple[list[str], str | None, tuple[str, ...]]:
    """The words from git's subcommand on, the folder git runs in, and the names of the settings
    the line gives git with -c or --config-env. Such a setting may define an alias the guard
    cannot read, or make a push delete."""
    settings = []
    while rest and rest[0].startswith("-"):
        option, joined, value = rest[0].partition("=")
        if not joined:
            value = rest[1] if option in GIT_VALUES and len(rest) > 1 else ""
        if option in ("-C", "--git-dir"):
            folder = _join(folder, value)
        if option in ("-c", "--config-env"):
            settings.append(value.split("=", 1)[0])
        rest = rest[2:] if not joined and option in GIT_VALUES else rest[1:]
    return rest, folder, tuple(settings)


def _runs_a_command(rest: list[str]) -> bool:
    """Whether git's own subcommand, with its words, runs a command or shell text from the line:
    `rebase -x`, `bisect run`, `submodule foreach` or `difftool --extcmd`, in any spelling."""
    forms = RUNS_A_COMMAND.get(rest[0], ())
    for word in rest[1:]:
        name = word.partition("=")[0]
        # a short `-x` may be joined to other letters or to its value, as in `-ix` or `-xmake`
        short = "-x" in forms and word.startswith("-") and not word.startswith("--") and "x" in word
        # git takes any unambiguous start of a long option, so `--ex` is `--exec`; `--e` is not
        shortened = name.startswith("--") and len(name) > 3 and any(form.startswith(name) for form in forms)
        if name in forms or short or shortened:
            return True
    return False


def _git_push(words: list[str], alias: Alias | None, base: str | None, seen: frozenset[str]) -> Push | None:
    """The push one `git` command runs, with the words after `push`, or None when it runs none.
    `alias` is None when the line may have set up an alias the guard cannot read. A push through
    an alias, or through a subcommand the guard cannot place, comes back with no words, so it is
    never in the plain shape."""
    rest, folder, settings = _git_options(words, base)
    if not rest:
        return None
    if rest[0] == "push":
        return Push(rest[1:], settings)
    if rest[0] in GIT_COMMANDS:
        # RULE: a git subcommand that runs a command named on the line is taken as a push
        return Push([]) if _runs_a_command(rest) else None
    name = rest[0].lower()
    runs = alias(rest[0], folder) if alias is not None and not settings and name not in seen else None
    # RULE: a subcommand the guard cannot place is taken as a push
    if runs is None:
        return Push([])
    line = runs[1:] if runs.startswith("!") else f"git {runs}"
    # RULE: a git alias is judged by what it runs
    return Push([]) if _pushes(line, alias, folder, seen | {name}) else None


def _deletes(push: Push) -> bool:
    """Whether a push deletes a branch: by option, by the start of one, by a setting given with
    it, or by a refspec with nothing before its colon."""
    flags = [a for a in push.args if a.startswith("-")]
    short = "".join(f[1:] for f in flags if not f.startswith("--"))
    names = [f[2:].split("=", 1)[0] for f in flags if f.startswith("--")]
    # RULE: any start of a deleting long option deletes
    if any(name and full.startswith(name) for name in names for full in DELETING) or "d" in short:
        return True
    # RULE: a deleting setting given before push deletes
    if any(DELETING_SETTING.fullmatch(name) for name in push.settings):
        return True
    return any(word.lstrip("+").startswith(":") for word in _positional(push.args)[1:])


def _set_up_to_delete(
    plain: bool, folder: str | None, push: Push, deletes_set_up: Callable[[str | None, str | None], bool] | None
) -> bool:
    """Whether the clone a plain push runs in has a deleting refspec or a mirror set up for its
    remote. A push in any other shape is refused or asked on its shape alone."""
    if not plain or deletes_set_up is None:
        return False
    positional = _positional(push.args)
    # RULE: a delete set up in the clones own settings is refused
    return deletes_set_up(folder, positional[0] if positional else None)


def _configured_deletes(git: Callable[..., str | None], remote: str) -> bool:
    """Whether a remote's settings make a push to it delete: a refspec with nothing before its
    colon, or a mirror."""
    refspecs = (git("config", "--get-all", f"remote.{remote}.push") or "").splitlines()
    mirror = (git("config", "--bool", "--get", f"remote.{remote}.mirror") or "").strip()
    return mirror == "true" or any(r.strip().lstrip("+").startswith(":") for r in refspecs)


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
    only_deletes: bool = False,
    deletes_set_up: Callable[[str | None, str | None], bool] | None = None,
    merges: bool = False,
    merges_only: bool = False,
) -> tuple[str, str] | None:
    """("deny" or "ask", why) for a command with a push or a merge in it, or None when it may run.
    `in_folder` reads another folder's remotes, or answers None when it cannot; `alias` reads
    what a git alias runs. With `only_deletes`, a readable push is judged only on whether it
    deletes a branch. `deletes_set_up(folder, remote)` says whether the clone's own settings
    make a push delete. Without `merges`, a merge or a status write through gh is refused; with
    `merges_only`, nothing else is judged."""
    found = None if merges else merge_or_status(command)
    # RULE: a merge or a status write through gh is refused to a role without merges
    if found is not None:
        return ("deny", f"This command {found}. Only the manager merges or sets a check result: ask the manager.")
    if merges_only:
        return None
    pushes = _pushes(command, alias)
    if not pushes:
        return None
    plain, folder = _shape(command)
    # RULE: a push that deletes a branch is refused in every mode
    if any(_deletes(push) for push in pushes) or _set_up_to_delete(plain, folder, pushes[0], deletes_set_up):
        return ("deny", "This push would delete a branch, and sessions never delete branches. Ask the owner.")
    if only_deletes:
        # RULE: without the guard a push the guard cannot read is refused
        return None if plain else ("deny", f"This push is not in a plain shape the guard can judge. Run {SHAPE}.")
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


def answer(
    hook_input: dict[str, Any],
    upstream: str,
    own_clone: bool = False,
    only_deletes: bool = False,
    merges: bool = False,
    merges_only: bool = False,
) -> dict[str, Any]:
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

    def deletes_set_up(folder: str | None, remote: str | None) -> bool:
        path = cwd if folder is None else os.path.join(cwd, os.path.expanduser(folder))
        return _configured_deletes(_git_at(path), remote or _reader(path)[1]())

    remote_url, default_remote = _reader(cwd)
    verdict = decide(
        command,
        upstream,
        own_clone,
        remote_url,
        default_remote,
        in_folder,
        alias=alias,
        only_deletes=only_deletes,
        deletes_set_up=deletes_set_up,
        merges=merges,
        merges_only=merges_only,
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
    parser.add_argument("--merges", action="store_true")
    options = parser.parse_args()
    try:
        hook_input = json.load(sys.stdin)
    except ValueError:
        return 0
    mode = options.push_mode
    out = answer(
        hook_input,
        options.upstream,
        own_clone=mode == OWN_CLONE,
        only_deletes=mode == NO_DELETES,
        merges=options.merges,
        merges_only=mode == MERGES_ONLY,
    )
    if out:
        sys.stdout.write(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
