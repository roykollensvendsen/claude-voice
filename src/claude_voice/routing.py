"""Who may answer a session's approval prompt: the owner always, a manager one day.

When a session asks to run a tool, its hook hands the prompt to the bridge. A manager
session is to answer the routine ones for the sessions under it, so the owner is asked
less. This module sorts each prompt, on the tool and its input alone and never on what
a session says about it:

- **the fixed list** (`FIXED`) names what only the owner may ever answer: secrets, force
  pushes, money, writes in someone else's name, the rules themselves, and the rest. A
  match goes to the owner, whatever else is true;
- **the shapes** say what a manager may answer at all: reading and editing inside the
  session's own repo, the everyday git and test commands, three bridge tools by name.
  Anything of no shape goes to the owner too.

Anything this module cannot read safely, such as a quote, a variable or a pipe in a
command, goes to the owner (fixed-list line 9). Where the data to judge a shape does not
exist yet, such as which branch is the session's own, the answer is the owner.

No manager answers yet: every prompt goes to the owner, and the sorting is recorded so
it can be checked first (docs/permissions.md, "Who answers a prompt").
"""

from __future__ import annotations

import fnmatch
import json
import logging
import os
import re
import stat
import subprocess
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from . import session_hook

log = logging.getLogger(__name__)

#: The bridge tools a manager may answer by name; never a pattern (managers-answer v2 §1a).
BRIDGE_SHAPES = frozenset({"health", "org_chart", "list_projects"})
#: Claude Code's own tools that are known but never a shape.
KNOWN = frozenset(
    {"Read", "Grep", "Glob", "Edit", "Write", "NotebookEdit", "Bash", "WebFetch", "WebSearch", "Agent", "Task", "Skill"}
)
SECRET_FILES = session_hook.SECRET_FILES  # permissions.json's secret files, pinned by a test there
SECRET_VALUE = re.compile(r"(?i)(token|api_?key|password|passwd|secret)[a-z0-9_]*=")
SECRET_KEY = re.compile(r"(?i)(token|api_?key|password|passwd|secret)")
MONEY = re.compile(r"(?i)(checkout|order|payment|purchase|pay_|_pay\b|rent)")
#: Command words that run other text as a command, so what runs cannot be read.
OPAQUE = frozenset(
    {"bash", "sh", "zsh", "dash", "fish", "eval", "xargs", "exec", "command", "env", "nice", "nohup"}
    | {"timeout", "sudo", "doas", "source", "time", "stdbuf", "setsid", "script", "watch", "parallel"}
)
SCRIPTING = frozenset({"python", "python3", "node", "perl", "ruby", "php", "deno", "bun"})
#: Characters that quote, expand, redirect, pipe or glob; a command holding one is not read.
UNREADABLE = re.compile(r"[\"'\\$`|<>(){}\[\]*?!#\n\r\t]")
NAME = re.compile(r"[a-z0-9][a-z0-9._+-]*")
READERS = frozenset({"ls", "cat", "head", "tail", "wc", "grep", "rg", "find", "sort", "jq", "diff"})
GIT_SHAPES = frozenset(
    {"status", "log", "diff", "show", "fetch", "add", "commit", "switch", "restore", "stash", "rebase"}
)
#: Paths under the home folder that hold secrets, matched on the realpath for every tool
#: (managers-answer v2.5). `*` matches across folders.
HOME_SECRETS = (
    ".netrc",
    ".git-credentials",
    ".aws/*",
    ".docker/config.json",
    ".config/gh/*",
    ".ssh/*",
    ".gnupg/*",
    ".local/share/keyrings/*",
    ".config/*key*",
    ".config/*token*",
    ".config/*secret*",
    ".config/*credential*",
)
#: File names that hold secrets wherever they are, the session's own repo included.
SECRET_NAMES = ("*.pem", "*.key", "id_*", ".env", ".env.*")
#: Templates of an env file; not secret when git tracks them as plain files in the repo.
ENV_TEMPLATES = frozenset({".env.example", ".env.sample", ".env.template"})
#: For each read tool: the options with no value, and the options with a value (how many words
#: it takes), managers-answer v2.7 to v2.9. Anything else on the command line goes to the owner.
OPTIONS: dict[str, tuple[frozenset[str], dict[str, int]]] = {
    "ls": (frozenset("-l -a -A -h -R -1 -t -S -r -d -F".split()), {}),
    "cat": (frozenset("-n -b -A -E -T -v".split()), {}),
    "head": (frozenset("-q -v".split()), {"-n": 1, "-c": 1}),
    "tail": (frozenset("-q -v".split()), {"-n": 1, "-c": 1}),
    "wc": (frozenset("-l -w -c -m -L".split()), {}),
    "grep": (
        frozenset("-i -n -r -l -L -c -v -w -x -E -F -o -h -H -s -q -z".split()),
        {"-e": 1, "-m": 1, "-A": 1, "-B": 1, "-C": 1, "--include": 1, "--exclude": 1, "--color": 1},
    ),
    "rg": (
        frozenset("-i -n -l -c -v -w -x -F -S -s -u --hidden --no-ignore --files -z".split()),
        {"-e": 1, "-g": 1, "-t": 1, "-T": 1, "-m": 1, "-A": 1, "-B": 1, "-C": 1},
    ),
    "sort": (frozenset("-n -r -u -h -V -f -b".split()), {"-k": 1, "-t": 1}),
    "jq": (frozenset("-r -c -e -S -j".split()), {"--arg": 2, "--argjson": 2}),
    "diff": (frozenset("-u -q -N -w -b".split()), {"-U": 1}),
}
FIND_OPTIONS = (frozenset({"-print", "-print0"}), {"-name", "-iname", "-path", "-type", "-maxdepth", "-mindepth"})
#: A recursive read's tree is walked before it is allowed (v2.10); past either bound, the owner.
WALK_ENTRIES = 20_000
WALK_SECONDS = 1.0
GH_WRITES = {
    "pr": {"create", "comment", "review", "merge", "close", "edit", "reopen", "ready"},
    "issue": {"create", "comment", "close", "edit", "reopen", "delete", "transfer", "lock"},
    "release": {"create", "edit", "delete", "upload"},
    "repo": {"create", "edit", "delete", "fork", "rename", "archive"},
    "gist": {"create", "edit", "delete"},
    "label": {"create", "edit", "delete"},
}
MAIN = frozenset({"main", "master"})
RC_FILES = (".bashrc", ".bash_profile", ".bash_login", ".profile", ".zshrc", ".zprofile", ".zshenv", ".gitconfig")
UV_TOOLS = frozenset({"pytest", "ruff", "mypy"})
#: The flags `uv sync` may take in a shape; any other could change where it installs from.
UV_SYNC_FLAGS = frozenset({"--locked", "--frozen", "--all-extras", "--dev", "-q", "--quiet"})
#: Per git subcommand: options that take no value, and options that take one. Anything
#: else, such as --output, --upload-pack or --exec, is no shape (managers-answer f3).
GIT_OPTIONS: dict[str, tuple[frozenset[str], dict[str, int]]] = {
    "status": (frozenset({"-s", "--short", "-b", "--branch", "--porcelain", "--ignored", "-q"}), {}),
    "log": (
        frozenset({"--oneline", "--graph", "--stat", "--decorate", "--all", "-p", "--name-only", "--name-status"})
        | frozenset({"--no-merges", "--reverse", "--first-parent", "--shortstat"})
        | frozenset(f"-{n}" for n in range(1, 10)),
        {"-n": 1, "--format": 1, "--pretty": 1, "--since": 1, "--until": 1, "--author": 1, "--grep": 1},
    ),
    "diff": (
        frozenset({"--stat", "--cached", "--staged", "--name-only", "--name-status", "--check", "--shortstat"})
        | frozenset({"--numstat", "--word-diff", "--no-color", "-q"}),
        {"-U": 1},
    ),
    "show": (frozenset({"--stat", "--name-only", "--name-status", "--oneline", "--no-patch", "-s"}), {"--format": 1}),
    "fetch": (frozenset({"-q", "--quiet", "--prune", "-p", "--all", "--tags"}), {}),
    "add": (frozenset({"-A", "--all", "-u", "--update", "-N", "--intent-to-add"}), {}),
    "commit": (
        frozenset({"-q", "--quiet", "-a", "--all", "--amend", "--no-edit", "--allow-empty"}),
        {"-m": 1, "--message": 1, "--fixup": 1},
    ),
    "switch": (frozenset({"-q", "--detach"}), {"-c": 1}),
    "restore": (frozenset({"--staged", "--worktree", "-q"}), {}),
    "stash": (frozenset({"-q", "-u", "--include-untracked"}), {"-m": 1}),
    "rebase": (frozenset({"-q", "--autosquash", "--continue", "--abort", "--skip"}), {"--onto": 1}),
}


@dataclass(frozen=True)
class Place:
    """Where the asking session works: its repo, its scratchpad, the home folder and the owner's
    account, and the folder it runs in, which relative paths are read against."""

    repo: Path
    scratchpad: Path | None
    home: Path
    owner: str
    cwd: Path | None = None
    also_reads: tuple[Path, ...] = ()  # a worktree's own repo: read there, never written

    @property
    def base(self) -> Path:
        # RULE: a relative path is read against the sessions own folder
        return self.cwd or self.repo

    def reads(self) -> list[str]:
        roots = [self.repo, *self.also_reads, self.scratchpad, self.home / ".cache" / "claude-voice" / "tmp"]
        return [os.path.realpath(root) for root in roots if root is not None]

    def writes(self) -> list[str]:
        return [os.path.realpath(self.repo)]  # its worktrees live inside it


@dataclass(frozen=True)
class Routing:
    """Who answers: always "roy" for now. `lines` are the fixed-list lines matched, `line` the first."""

    to: str
    lines: tuple[int, ...]
    shape: bool
    why: list[str] = field(default_factory=list, compare=False)

    @property
    def line(self) -> int | None:
        return self.lines[0] if self.lines else None


@dataclass
class Ask:
    """One prompt, as the checks see it."""

    tool: str
    given: dict[str, Any]
    place: Place | None
    now: datetime
    machines: list[str]
    run_branches: list[str]
    parts: list[list[str]] | None  # a Bash command's simple commands, or None when it cannot be read
    command: str = ""
    tracked: Callable[[Path], bool] = lambda path: False  # whether git tracks a file in the repo
    why: list[str] = field(default_factory=list)  # what a check found, to tell the owner

    def words(self) -> Iterable[list[str]]:
        return self.parts or []


# -- reading a command ---------------------------------------------------------------------


def split_command(command: str) -> list[list[str]] | None:
    """A command's simple commands, joined by && or ;, each as its words; None when any
    part quotes, expands, pipes, redirects, globs or runs another command's text."""
    if not command.isascii() or not command.strip():
        return None
    joined = command.replace("&&", ";")
    if "&" in joined or UNREADABLE.search(joined):
        return None
    parts = [part.split() for part in joined.split(";") if part.strip()]
    for words in parts:
        if any(word.startswith("~") for word in words) or not NAME.fullmatch(words[0]):
            return None
        if words[0] in OPAQUE or (words[0] in SCRIPTING and len(words) > 1):
            return None
    return parts


def _values(words: list[str], *flags: str) -> list[str]:
    """The values given to any of `flags`, as `--flag value` or `--flag=value`."""
    out = []
    for i, word in enumerate(words):
        for flag in flags:
            if word == flag and i + 1 < len(words):
                out.append(words[i + 1])
            elif flag.startswith("--") and word.startswith(flag + "="):
                out.append(word.split("=", 1)[1])
    return out


def _owner_of(remote: str) -> str | None:
    """The account a GitHub URL or owner/repo names; None when it names none."""
    match = re.search(r"github\.com[:/]([^/]+)/", remote) or re.fullmatch(r"([^/:@]+)/[^/:@]+", remote)
    return match.group(1).lower() if match else None


def _push_args(words: list[str]) -> tuple[list[str], list[str]]:
    """A git push's flags and its other words (the remote, then the refspecs)."""
    rest = words[2:]
    return [w for w in rest if w.startswith("-")], [w for w in rest if not w.startswith("-")]


def _short_flags(flags: list[str]) -> str:
    return "".join(f[1:] for f in flags if f.startswith("-") and not f.startswith("--"))


# -- the fixed list, one check per line ---------------------------------------------------


def _path_matches(path: str, place: Place | None, patterns: Iterable[str]) -> bool:
    full = path if place is None or os.path.isabs(path) else os.path.join(place.base, path)
    return any(fnmatch.fnmatch(full, pattern) or fnmatch.fnmatch(path, pattern) for pattern in patterns)


def _file_paths(ask: Ask) -> list[str]:
    keys = ("file_path", "notebook_path", "path")
    return [str(ask.given[key]) for key in keys if isinstance(ask.given.get(key), str)]


def _writes(ask: Ask) -> list[str]:
    """Paths the prompt may write: an edit's file, or the words of a command that is not a reader."""
    if ask.tool in ("Edit", "Write", "NotebookEdit"):
        return _file_paths(ask)
    return [w for words in ask.words() if words[0] not in READERS and words[0] != "git" for w in words[1:]]


def _loose_words(command: str, home: str) -> list[str]:
    """A command's words read loosely, quotes dropped and the home folder spelled out, so a
    secret path is found even in a command too tangled to read safely."""
    text = re.sub(r"[\"'\\]", "", command)
    # A function, so a backslash in the home folder (C:\Users) is never read as an escape.
    text = re.sub(r"\$\{?HOME\}?", lambda _: home, text)
    words = re.split(r"[\s;&|<>()`=]+", text)
    return [home + w[1:] if w.startswith("~/") or w == "~" else w for w in words if w]


def _template(path: str, ask: Ask) -> bool:
    """An env template git tracks as a plain file in the repo, whose realpath is itself."""
    if ask.place is None or os.path.basename(path) not in ENV_TEMPLATES:
        return False
    full = os.path.abspath(os.path.join(ask.place.base, path))
    inside = full.startswith(os.path.realpath(ask.place.repo) + os.sep)
    # RULE: a tracked env template is not secret
    return not os.path.islink(full) and os.path.realpath(full) == full and inside and ask.tracked(Path(full))


#: The secret table's own paths under home, searched for the inodes a hard link could share.
SECRET_HOMES = (
    ".netrc",
    ".git-credentials",
    ".aws",
    ".docker/config.json",
    ".config/gh",
    ".config/claude-voice",
    ".ssh",
    ".gnupg",
    ".local/share/keyrings",
    ".claude/.credentials.json",
)
#: Names directly in ~/.config that the table names; each one found is searched too.
CONFIG_SECRETS = ("*key*", "*token*", "*secret*", "*credential*", "dreame*", "tv-mcp*")
#: Folders whose files are hard-linked by their tools (uv, npm, git); never searched for.
LINKED_FOLDERS = frozenset({".venv", "node_modules", ".git"})
INODES_SECONDS = 60.0
#: home -> (when found, the inodes, or None when the search ran past its bound)
SECRET_INODES: dict[str, tuple[float, frozenset[tuple[int, int]] | None]] = {}


def _named_secret(path: str, ask: Ask) -> bool:
    """Whether `path`, as written or through every symbolic link, is named as a secret."""
    home = str(ask.place.home) if ask.place else os.path.expanduser("~")
    base = ask.place.base if ask.place else Path.cwd()
    full = os.path.abspath(os.path.join(base, os.path.expanduser(path)))
    patterns = [*SECRET_FILES, *(f"{home}/{name}" for name in HOME_SECRETS)]
    for seen in {full, os.path.realpath(full)}:
        if any(fnmatch.fnmatch(seen, pattern) for pattern in patterns):
            return True
        name = os.path.basename(seen)
        if any(fnmatch.fnmatch(name, pattern) for pattern in SECRET_NAMES) and not _template(path, ask):
            return True
    return False


def _search_inodes(home: Path) -> frozenset[tuple[int, int]] | None:
    """The (device, inode) of the regular files at the secret table's own paths under home,
    never following a link; None when the search runs past the walk's bounds."""
    clock, seen, found = time.monotonic(), 0, set()
    paths = [str(home / name) for name in SECRET_HOMES]
    try:
        config = list(os.scandir(home / ".config"))
    except OSError:
        config = []
    paths += [e.path for e in config if any(fnmatch.fnmatch(e.name, p) for p in CONFIG_SECRETS)]
    while paths:
        top = paths.pop()
        seen += 1
        if time.monotonic() - clock > WALK_SECONDS or seen > WALK_ENTRIES:
            return None  # the caller sends a hard-linked file to the owner
        try:
            info = os.lstat(top)
            if stat.S_ISDIR(info.st_mode):
                paths.extend(e.path for e in os.scandir(top))
            elif stat.S_ISREG(info.st_mode):
                found.add((info.st_dev, info.st_ino))
        except OSError:
            continue
    return frozenset(found)


def _secret_inodes(ask: Ask) -> frozenset[tuple[int, int]] | None:
    """The secret files' inodes, searched at most once a minute for each home folder. Two
    sorting threads may both search at once; that is harmless, as either result is right."""
    home = ask.place.home if ask.place else Path.home()
    when, inodes = SECRET_INODES.get(str(home), (-INODES_SECONDS, None))
    if time.monotonic() - when >= INODES_SECONDS:
        inodes = _search_inodes(home)
        SECRET_INODES[str(home)] = (time.monotonic(), inodes)
    return inodes


def _hard_linked(full: str, ask: Ask) -> bool:
    """Whether `full` is a second name for one of the secret table's files."""
    try:
        info = os.stat(full)
    except OSError:
        return False
    if info.st_nlink < 2 or not stat.S_ISREG(info.st_mode):
        return False  # a folder always has several names
    inodes = _secret_inodes(ask)
    if inodes is not None:
        return (info.st_dev, info.st_ino) in inodes  # a known set is always looked in
    if LINKED_FOLDERS.intersection(Path(full).parts):
        log.debug("a hard link in a folder its tools link, let through past the bound: %s", full)
        return False
    # RULE: a home too large to search for hard links sends a linked file to the owner
    ask.why.append(f"{full}: a hard link, and the secret files could not all be checked")
    return True


def is_secret(path: str, ask: Ask) -> bool:
    """Whether `path` is a file that holds secrets: by its name, through a symbolic link, or as
    a hard link to one of the secret table's files."""
    if _named_secret(path, ask):
        return True
    base = ask.place.base if ask.place else Path.cwd()
    # RULE: a hard link to a secret goes to the owner
    return _hard_linked(os.path.join(base, os.path.expanduser(path)), ask)


def secrets(ask: Ask) -> bool:
    """Line 1: the secret files, logging in, keys, and any value set for a token or password."""
    words = [w for part in ask.words() for w in part]
    home = str(ask.place.home) if ask.place else os.path.expanduser("~")
    # RULE: line 1 every secret path goes to the owner for any tool
    if any(is_secret(p, ask) for p in _file_paths(ask) + _loose_words(ask.command, home)):
        return True
    if any(SECRET_VALUE.search(w) or w == "secret-box" for w in words):
        return True
    for part in ask.words():
        if part[:2] in (["gh", "auth"], ["git", "credential"]) or part[0] in ("ssh-keygen", "ssh-add"):
            return True
    mcp = ask.tool.startswith("mcp__")
    return mcp and any(SECRET_KEY.search(str(key)) for key in ask.given)


def force_push(ask: Ask) -> bool:
    """Line 2: a push that can overwrite, a lease included until the session's own branch is known."""
    for part in ask.words():
        if part[:2] != ["git", "push"]:
            continue
        flags, others = _push_args(part)
        if "f" in _short_flags(flags) or any(f.startswith("--force") for f in flags):
            return True
        if any(w.startswith("+") for w in others[1:]):
            return True
    return False


def money(ask: Ask) -> bool:
    """Line 3: a tool that pays, orders or rents. A plugin's own money mark is read in step 2."""
    return ask.tool.startswith("mcp__") and bool(MONEY.search(ask.tool.rsplit("__", 1)[-1]))


def _gh_api_writes(part: list[str]) -> tuple[bool, str]:
    """Whether a `gh api` call writes, and the path it names."""
    method = (_values(part, "-X", "--method") or ["GET"])[-1].upper()
    fields = any(w in ("-f", "-F", "--field", "--raw-field", "--input") or w.startswith("--field=") for w in part)
    takes_value = {"-X", "--method", "-f", "-F", "--field", "--raw-field", "--input", "-H", "--header", "-q", "--jq"}
    path = next((w for i, w in enumerate(part[2:], 2) if not w.startswith("-") and part[i - 1] not in takes_value), "")
    return method != "GET" or fields, path


def _elsewhere(part: list[str], own: str) -> bool:
    """Whether one command writes in public in an account other than `own`."""
    if part[0] == "gh" and len(part) > 2 and part[2] in GH_WRITES.get(part[1], ()):
        return any(_owner_of(r) != own for r in _values(part, "-R", "--repo"))
    if part[:2] == ["gh", "api"]:
        writes, path = _gh_api_writes(part)
        owner = path.split("/")[1].lower() if path.startswith("repos/") and path.count("/") > 1 else None
        return writes and owner != own
    if part[:2] == ["git", "push"]:
        others = _push_args(part)[1]
        return bool(others) and ("://" in others[0] or "@" in others[0]) and _owner_of(others[0]) != own
    return False


def outside_account(ask: Ask) -> bool:
    """Line 4: a public write in an account other than the owner's."""
    own = ask.place.owner.lower() if ask.place else ""
    return any(_elsewhere(part, own) for part in ask.words())


RULE_FILES = (
    "*/.claude/settings*.json",
    "*CLAUDE*.md",
    "*AGENTS.md",
    "*/.claude/hooks/*",
    "*/.claude/roles/*",
    "*/.claude/agents/*",
)


def rules_and_config(ask: Ask) -> bool:
    """Line 5: the permission rules, the instruction files, and every configuration command."""
    home = str(ask.place.home) if ask.place else "~"
    patterns = (*RULE_FILES, f"{home}/.claude/*", f"{home}/.config/claude-voice/*")
    if any(_path_matches(p, ask.place, patterns) for p in _writes(ask)):
        return True
    for part in ask.words():
        pair = part[:2]
        if pair in (["claude-voice", "permissions"], ["cv", "roles"], ["claude-voice", "roles"]):
            return True
        if pair in (["claude", "config"], ["git", "config"]) or part[0] in ("chmod", "chown", "chgrp"):
            return True
    return False


def plugin_approval(ask: Ask) -> bool:
    """Line 6: approving a plugin, and every tool that confirms or approves."""
    name = ask.tool.rsplit("__", 1)[-1]
    if ask.tool.startswith("mcp__") and (name.endswith("_approve") or name.endswith("_confirm")):
        return True
    return any(part[:3] == ["claude-voice", "plugins", "approve"] for part in ask.words())


def standing_rules(ask: Ask) -> bool:
    """Line 8: deleting branches, recursive removes, merges, other machines, starting and stopping the bridge."""
    name = ask.tool.rsplit("__", 1)[-1]
    if ask.tool.startswith("mcp__") and (
        name in ("plugin_restart", "update_bridge") or any(name.startswith(m + "_") for m in ask.machines)
    ):
        return True
    return any(_standing(part) for part in ask.words())


def _deletes(part: list[str]) -> bool:
    """Whether one command deletes a branch or removes a tree."""
    flags = [w for w in part[1:] if w.startswith("-")]
    short = set(_short_flags(flags))
    if part[:2] == ["git", "push"]:
        others = _push_args(part)[1]
        return "--delete" in flags or "d" in short or any(w.startswith(":") for w in others[1:])
    if part[:2] == ["git", "branch"]:
        return "--delete" in flags or bool(short & {"d", "D"})
    if part[0] == "rm":
        return "--recursive" in flags or bool(short & {"r", "R"})
    return False


def _standing(part: list[str]) -> bool:
    if _deletes(part):
        return True
    if part[:3] == ["gh", "pr", "merge"]:  # step 2 lets a manager merge the session's own pull request
        return True
    if part[:2] == ["gh", "api"]:
        method = (_values(part, "-X", "--method") or ["GET"])[-1].upper()
        return method == "DELETE" and any("/git/refs" in w for w in part)
    return (
        part[0] in ("claude-voice", "cv")
        and len(part) > 1
        and part[1] in ("serve", "restart", "stop", "start", "update")
    )


def main_and_admin(ask: Ask) -> bool:
    """Line 10: a push to main, master or a run branch, an admin merge, and branch protection."""
    branches = MAIN | set(ask.run_branches)
    for part in ask.words():
        if part[:2] == ["git", "push"]:
            for spec in _push_args(part)[1][1:]:
                target = spec.lstrip("+").split(":")[-1].removeprefix("refs/heads/")
                if target in branches:
                    return True
        if part[:3] == ["gh", "pr", "merge"] and "--admin" in part:
            return True
        if part[:2] == ["gh", "api"] and any(w.rstrip("/").endswith("/protection") for w in part):
            return True
    return False


def typing_into_panes(ask: Ask) -> bool:
    """Line 11: keys typed into another session's terminal."""
    for part in ask.words():
        if part[0] == "tmux" and any(
            w in ("send-keys", "send", "paste-buffer", "pasteb", "load-buffer", "loadb") for w in part
        ):
            return True
        if part[0] == "screen" and "-X" in part and "stuff" in part:
            return True
    return False


def bridge_state(ask: Ask) -> bool:
    """Line 13: any write to the bridge's own state, such as the session list or the prompt book."""
    home = str(ask.place.home) if ask.place else "~"
    return any(_path_matches(p, ask.place, (f"{home}/.local/state/claude-voice/*",)) for p in _writes(ask))


def start_up_and_identity(ask: Ask) -> bool:
    """Line 14: what runs at start-up or speaks for the owner: shell files, services, keys, git hooks."""
    home = str(ask.place.home) if ask.place else "~"
    patterns = (
        *(f"{home}/{name}" for name in RC_FILES),
        f"{home}/.config/systemd/*",
        f"{home}/.ssh/*",
        "*/.git/hooks/*",
    )
    if any(_path_matches(p, ask.place, patterns) or "core.hooksPath" in p for p in _writes(ask)):
        return True
    for part in ask.words():
        if part[0] in ("crontab", "at", "batch") or (part[0] == "systemctl" and "--user" in part):
            return True
        if "core.hooksPath" in part:
            return True
    return False


def _oslo_hour(now: datetime) -> int:
    """The hour in Oslo: UTC+1, and UTC+2 from 01.00 UTC on March's last Sunday to October's."""
    at = now.astimezone(UTC)

    def last_sunday(month: int) -> datetime:
        last = datetime(at.year, month + 1, 1, 1, tzinfo=UTC) - timedelta(days=1)
        return last - timedelta(days=(last.weekday() + 1) % 7)

    summer = last_sunday(3) <= at < last_sunday(10)
    return (at + timedelta(hours=2 if summer else 1)).hour


def night_merges(ask: Ask) -> bool:
    """Line 15: a merge between 23.00 and 07.00 in Oslo."""
    if 7 <= _oslo_hour(ask.now) < 23:
        return False
    return any(part[:3] == ["gh", "pr", "merge"] for part in ask.words()) or main_and_admin(ask)


def outside_sandbox(ask: Ask) -> bool:
    """Line 16: a command asked to run outside the sandbox."""
    return bool(ask.given.get("dangerouslyDisableSandbox"))


def made_public(ask: Ask) -> bool:
    """Line 17: making something public on the owner's own account."""
    for part in ask.words():
        three = part[:3]
        if three in (["gh", "gist", "create"], ["gh", "repo", "create"], ["gh", "release", "create"]):
            return True
        if three == ["gh", "repo", "edit"] and any(w.startswith("--visibility") for w in part):
            return True
        if part[:2] == ["gh", "api"] and any(w.rstrip("/").endswith("/pages") for w in part):
            return True
    return False


def installs(ask: Ask) -> bool:
    """Line 18: anything installed from the internet."""
    own = ask.place.owner.lower() if ask.place else ""
    for part in ask.words():
        two, three = part[:2], part[:3]
        if part[0] in ("npx", "uvx", "pipx") or two in (["pip", "install"], ["pip3", "install"], ["uv", "add"]):
            return True
        if three in (["uv", "pip", "install"], ["uv", "tool", "install"]) or two in (
            ["cargo", "install"],
            ["go", "install"],
        ):
            return True
        if part[0] in ("npm", "pnpm", "yarn") and len(part) > 1 and part[1] in ("install", "i", "add", "ci"):
            return True
        if two == ["git", "clone"] and any(_owner_of(w) not in (own,) for w in part[2:] if not w.startswith("-")):
            return True
    return False


def stops_processes(ask: Ask) -> bool:
    """Line 19: stopping a process or a service, however it is spelled."""
    for part in ask.words():
        if part[0] in ("kill", "pkill", "killall"):
            return True
        if part[0] == "systemctl" and any(w in ("stop", "restart", "kill") for w in part):
            return True
        if part[0] == "docker" and len(part) > 1 and part[1] in ("stop", "kill"):
            return True
        if part[0] == "tmux" and any(w.startswith("kill-") for w in part):
            return True
    return False


def _never(ask: Ask) -> bool:
    return False


#: The fixed list: what only the owner answers (managers-answer v2 §1). Lines 7 and 9 are
#: decided in `sort`, since they need the owner's own patterns and the reading of the input.
FIXED: list[tuple[int, Callable[[Ask], bool]]] = [
    (1, secrets),
    (2, force_push),
    (3, money),
    (4, outside_account),
    (5, rules_and_config),
    (6, plugin_approval),
    (7, _never),
    (8, standing_rules),
    (9, _never),
    (10, main_and_admin),
    (11, typing_into_panes),
    (12, _never),  # step 2: a plugin tool not marked read only; until then no plugin tool is a shape
    (13, bridge_state),
    (14, start_up_and_identity),
    (15, night_merges),
    (16, outside_sandbox),
    (17, made_public),
    (18, installs),
    (19, stops_processes),
]


# -- the shapes ------------------------------------------------------------------------------


def _inside(path: str, roots: list[str], base: Path) -> bool:
    """Whether `path`, symlinks followed, is in one of `roots`."""
    if path.startswith("/proc") or path.startswith("~"):
        return False
    real = os.path.realpath(os.path.join(base, path))
    return any(real == root or real.startswith(root + os.sep) for root in roots)


def _editable(path: str, place: Place) -> bool:
    if not _inside(path, place.writes(), place.base):
        return False
    rel = Path(os.path.relpath(os.path.realpath(os.path.join(place.base, path)), os.path.realpath(place.repo)))
    return ".git" not in rel.parts and rel.parts[:2] != (".github", "workflows")


def _git_shape(part: list[str], place: Place) -> bool:
    sub = part[1] if len(part) > 1 else ""
    if sub == "worktree":
        return part[2:3] == ["add"] and all(
            _inside(w, place.writes(), place.base) for w in part[3:] if not w.startswith("-")
        )
    if sub not in GIT_SHAPES:
        return False
    no_value, with_value = GIT_OPTIONS[sub]
    # RULE: a git option off its subcommands list is no shape
    plain = _options(part[1:], no_value, with_value)
    if plain is None:
        return False
    return _git_words_fit(sub, plain, place)


def _git_words_fit(sub: str, plain: list[str], place: Place) -> bool:
    """Whether a git command's plain words are of its shape: a fetch names a remote, and an
    add or restore names paths the session may write."""
    if sub == "fetch":
        # RULE: git fetch names a remote never a url or a path
        return all(NAME.fullmatch(w.lower()) and "/" not in w for w in plain[:1]) and all(
            ":" not in w and not w.startswith((".", "/", "~")) for w in plain
        )
    if sub in ("add", "restore"):
        return all(_inside(w, place.writes(), place.base) for w in plain)
    return True


def _options(part: list[str], no_value: frozenset[str], with_value: dict[str, int]) -> list[str] | None:
    """The plain words of a read tool's command, or None when any option is not on its list.
    A value option takes its value joined (`-n5`), after `=` (long only), or as the next words."""
    plain: list[str] = []
    i, ended = 1, False
    while i < len(part):
        word = part[i]
        i += 1
        if ended or not word.startswith("-") or word == "-":
            plain.append(word)
        elif word == "--":
            ended = True
        elif word.startswith("--"):
            name, eq, _ = word.partition("=")
            if name in no_value and not eq:
                continue
            if name not in with_value or (eq and with_value[name] != 1):
                return None
            i += 0 if eq else with_value[name]
        else:
            took = _bundle(word, no_value, with_value)
            if took is None:
                return None
            i += took
        if i > len(part):
            return None  # a value option with nothing after it
    return plain


def _bundle(word: str, no_value: frozenset[str], with_value: dict[str, int]) -> int | None:
    """How many next words a bundle of short options takes, or None for a letter off the list.
    Letters are read one at a time until a value option, and the rest of the word is its value."""
    for at, letter in enumerate(word[1:], 1):
        option = "-" + letter
        if option in with_value:
            return 0 if word[at + 1 :] else with_value[option]
        if option not in no_value:
            return None
    return 0


def _find_plain(part: list[str]) -> list[str] | None:
    """find's start points, or None when its expression holds anything off the list."""
    no_value, with_value = FIND_OPTIONS
    first = next((i for i, w in enumerate(part[1:], 1) if w.startswith("-")), len(part))
    starts, rest = part[1:first], part[first:]
    i = 0
    while i < len(rest):
        if rest[i] in no_value:
            i += 1
        elif rest[i] in with_value and i + 1 < len(rest):
            i += 2
        else:
            return None
    return starts


def _recursive(part: list[str]) -> bool:
    """Whether a read tool walks a tree: rg and find always, grep with -r, ls with -R."""
    letter = {"grep": "r", "ls": "R"}.get(part[0])
    if letter is None:
        return part[0] in ("rg", "find")
    return any(w.startswith("-") and not w.startswith("--") and letter in w for w in part[1:])


def _walk_clean(start: str, place: Place, ask: Ask) -> bool:
    """Walk a tree the way grep -r does, never following a link and skipping .git, and say
    whether it holds no secret. A secret found, or a tree past the bounds, is put in ask.why."""
    clock = time.monotonic()
    folders, seen = [os.path.join(place.base, start)], 0
    while folders:
        try:
            entries = list(os.scandir(folders.pop()))
        except OSError:
            continue  # grep -r skips what it cannot open; nothing is read from it
        for entry in entries:
            seen += 1
            # RULE: a recursive read over a tree past the bound goes to the owner
            if seen > WALK_ENTRIES or time.monotonic() - clock > WALK_SECONDS:
                ask.why.append(f"{start}: too large to check")
                return False
            # RULE: a recursive read over a secret goes to the owner and names it
            if is_secret(entry.path, ask) and not entry.is_symlink():
                ask.why.append(os.path.relpath(entry.path, place.repo))
                return False
            if entry.is_dir(follow_symlinks=False) and entry.name != ".git":
                folders.append(entry.path)
    return True


def _gives_pattern(part: list[str]) -> bool:
    """Whether a grep or rg names its pattern with -e, so no plain word is the pattern."""
    flags = part[1 : part.index("--")] if "--" in part else part[1:]
    return any(w.startswith("-") and not w.startswith("--") and "e" in w for w in flags)


def _read_paths(part: list[str]) -> list[str] | None:
    """The paths a read tool reads, or None when it uses an option off its list."""
    if part[0] == "find":
        return _find_plain(part)
    no_value, with_value = OPTIONS[part[0]]
    plain = _options(part, no_value, with_value)
    if plain is None:
        return None
    pattern_first = part[0] in ("grep", "rg") and "--files" not in part and not _gives_pattern(part)
    return plain[1:] if part[0] == "jq" or pattern_first else plain


def _reader_shape(part: list[str], place: Place, ask: Ask) -> bool:
    """A read tool with only listed options, reading only inside the session's areas."""
    paths = _read_paths(part)
    if paths is None or "-" in paths:
        return False
    inside = all(_inside(p, place.reads(), place.base) and not is_secret(p, ask) for p in paths or ["."])
    if inside and _recursive(part):
        inside = all(_walk_clean(p, place, ask) for p in paths or ["."])
    # RULE: diff takes two regular files only
    if part[0] == "diff":
        return inside and len(paths) == 2 and all(os.path.isfile(os.path.join(place.base, p)) for p in paths)
    # RULE: a read outside the areas goes to the owner
    return inside


def _uv_shape(part: list[str], place: Place) -> bool:
    if part[1:2] == ["sync"]:
        # RULE: uv sync takes only the listed flags
        return all(w in UV_SYNC_FLAGS for w in part[2:])
    if part[1:2] != ["run"] or len(part) < 3:
        return False
    first = part[2]
    return first in UV_TOOLS or (
        "/" in first and _inside(first, place.writes(), place.base) and Path(place.base, first).is_file()
    )


def _command_shape(part: list[str], place: Place, ask: Ask) -> bool:
    """Whether one simple command is of a shape a manager may answer."""
    head = part[0]
    if head == "git":
        return _git_shape(part, place)
    if head == "gh":
        return part[1:3] in (
            ["pr", "view"],
            ["pr", "checks"],
            ["pr", "diff"],
            ["pr", "list"],
            ["run", "view"],
            ["run", "rerun"],
        )
    if head == "uv":
        return _uv_shape(part, place)
    if head in READERS:
        return _reader_shape(part, place, ask)
    if head in ("mkdir", "cp", "mv"):
        return all(_editable(w, place) for w in part[1:] if not w.startswith("-"))
    return head == "claude-voice" and part[1:2] in (["work"], ["tidy"])


def _file_shape(ask: Ask, place: Place) -> bool:
    paths = _file_paths(ask) or ([str(place.repo)] if ask.tool in ("Grep", "Glob") else [])
    pattern = str(ask.given.get("pattern", ""))
    if ask.tool == "Glob" and (pattern.startswith("/") or ".." in pattern):
        return False
    if ask.tool in ("Read", "Grep", "Glob"):
        inside = bool(paths) and all(_inside(p, place.reads(), place.base) for p in paths)
        # RULE: the grep tool on a folder walks it first
        return inside and (ask.tool != "Grep" or all(_walk_clean(p, place, ask) for p in paths))
    return bool(paths) and all(_editable(p, place) for p in paths)


def _mcp_shape(tool: str, read_only: Iterable[str]) -> bool:
    try:
        _, server, name = tool.split("__", 2)
    except ValueError:
        return False
    if name in read_only:
        return True
    return server.endswith("claude-voice") and name in BRIDGE_SHAPES


def _shape(ask: Ask, read_only: Iterable[str]) -> bool:
    if ask.tool.startswith("mcp__"):
        return _mcp_shape(ask.tool, read_only)
    if ask.place is None:
        return False
    if ask.tool == "Bash":
        return ask.parts is not None and all(_command_shape(part, ask.place, ask) for part in ask.parts)
    return ask.tool in ("Read", "Grep", "Glob", "Edit", "Write", "NotebookEdit") and _file_shape(ask, ask.place)


# -- sorting ---------------------------------------------------------------------------------


def _git_out(folder: str, *args: str) -> str | None:
    try:
        found = subprocess.run(["git", "-C", folder, *args], capture_output=True, text=True, timeout=5, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    return found.stdout.strip() if found.returncode == 0 and found.stdout.strip() else None


def toplevel(cwd: str) -> Path | None:
    """The top folder of the git checkout `cwd` is in, or None when git doesn't know it."""
    top = _git_out(cwd, "rev-parse", "--show-toplevel")
    return Path(top) if top else None


def checkout_of(cwd: str) -> tuple[Path, Path] | None:
    """The checkout `cwd` is in and the repo it belongs to. A plain clone is both. A worktree
    belongs to the repo holding git's common folder, and only when that repo lists it as its
    own worktree, so a .git file written to point elsewhere is not taken at its word."""
    top, common = toplevel(cwd), _git_out(cwd, "rev-parse", "--path-format=absolute", "--git-common-dir")
    if top is None or common is None:
        return None
    main = Path(os.path.realpath(common)).parent
    if main == Path(os.path.realpath(top)):
        return top, main
    listed = _git_out(str(main), "worktree", "list", "--porcelain") or ""
    own = {os.path.realpath(line[len("worktree ") :]) for line in listed.splitlines() if line.startswith("worktree ")}
    # RULE: a forged git file gets no place
    return (top, main) if os.path.realpath(top) in own else None


def place_for(
    cwd: str,
    known: Iterable[Path],
    home: Path,
    owner: str,
    locate: Callable[[str], tuple[Path, Path] | None] = checkout_of,
) -> Place | None:
    """Where a session runs: its checkout, when the repo it belongs to is one the org chart
    knows. It writes only in its checkout and reads there and in the repo. Anywhere else has
    no place, so nothing it asks is of a shape (managers-answer f1)."""
    found = locate(cwd)
    # RULE: a sessions place is a repo the chart knows never its folder as given
    if found is None or found[1].resolve() not in {Path(k).resolve() for k in known}:
        return None
    top, main = found
    also = (main,) if main.resolve() != top.resolve() else ()
    return Place(repo=top, scratchpad=None, home=home, owner=owner, cwd=Path(cwd), also_reads=also)


def _git_tracked(path: Path, place: Place | None) -> bool:
    """Whether git tracks `path` in the session's repo."""
    if place is None:
        return False
    try:
        found = subprocess.run(
            ["git", "-C", str(place.repo), "ls-files", "--error-unmatch", "--", str(path)],
            capture_output=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return found.returncode == 0


def sort(
    tool: str,
    given: Any,
    *,
    place: Place | None,
    now: datetime,
    cut: bool = False,
    also: Iterable[str] = (),
    machines: Iterable[str] = (),
    run_branches: Iterable[str] = (),
    read_only: Iterable[str] = (),
    tracked: Callable[[Path], bool] | None = None,
) -> Routing:
    """Sort one approval prompt: the fixed-list lines it matches, and whether it is of a shape."""
    given = given if isinstance(given, dict) else {}
    command = str(given.get("command", "")) if tool == "Bash" else ""
    parts = split_command(command) if tool == "Bash" else []
    known = tracked or (lambda path: _git_tracked(path, place))
    ask = Ask(tool, given, place, now, list(machines), list(run_branches), parts, command, known)
    lines = [number for number, matches in FIXED if matches(ask)]
    text = command if tool == "Bash" else f"{tool} {json.dumps(given, sort_keys=True)}"
    # RULE: line 7 the owners own list adds to what reaches the owner
    if any(fnmatch.fnmatch(text, pattern) for pattern in also):
        lines.append(7)
    # RULE: line 9 what cannot be read safely goes to the owner
    if cut or parts is None or (tool not in KNOWN and not tool.startswith("mcp__")):
        lines.append(9)
    lines = sorted(set(lines))
    # RULE: a command of no allowed shape goes to the owner
    shape = not lines and _shape(ask, read_only)
    # RULE: every prompt goes to the owner while no manager answers
    return Routing("roy", tuple(lines), shape, ask.why)
