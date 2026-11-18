"""The session hook: two routine steps a session may take without asking the owner.

Claude Code runs it before every shell command in the bridge's repositories (a
PreToolUse hook). It lets through:

- a plain remove whose every target lies inside the session's own scratchpad;
- an issue filed in one of the repositories of the session's own programme, whose
  title and body hold no variable, no secret's shape and none of the owner's
  personal terms, and whose body file is in the scratchpad or tracked by git.

Every other recursive remove, however it is spelled, and every other issue, it asks
the owner about. A single-file rm and `git rm` keep the rules they always had. All
other commands it leaves to Claude Code's own rules. When it cannot tell, it asks.

It runs as ``python3 -I session_hook.py --repos owner/name,... [--terms FILE]``,
with the standard library only, so a broken bridge environment cannot stop it.
"""

from __future__ import annotations

import argparse
import fnmatch
import importlib.util
import json
import os
import re
import shlex
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from types import ModuleType
from typing import Any


def _beside(name: str) -> ModuleType:
    """A module of the bridge's that sits beside this file and uses the standard library only,
    loaded by its path: run as a script, this file has no package to import from."""
    spec = importlib.util.spec_from_file_location(f"_session_hook_{name}", Path(__file__).with_name(f"{name}.py"))
    if spec is None or spec.loader is None:
        raise ImportError(f"{name}.py is not beside the session hook")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# Claude Code runs this file with the system's python3, where the bridge's package isn't installed.
# RULE: the session hook runs with the standard library only
if __package__:
    from . import safe_git
else:
    safe_git = _beside("safe_git")

ALLOW, ASK, DENY = "allow", "ask", "deny"

#: Anything that chains, pipes, redirects or substitutes: such a command is never let through.
CONTROL = re.compile(r"[;&|<>`\n\r]|\$\(")
#: Anything the shell would expand: the hook cannot know what it names, so it asks.
EXPANDS = re.compile(r"[*?\[\]{}~$]")
#: Quotes and backslashes, which the shell takes away before it runs a word.
QUOTING = re.compile(r"[\"'\\]")
#: What makes the shell build a word: a variable, a substitution or a glob.
BUILT = re.compile(r"[$`*?\[\]{}]")
#: A word that ends a shell command, so the next word is a command again.
SEPARATORS = re.compile(r"[;&|()`\n]")
REMOVE_OPTIONS = re.compile(r"-[rRf]+|--recursive|--force")
#: The secret files the rules refuse to read: the same list as permissions.json's secrets.files.
SECRET_FILES = (
    "*.config/claude-voice/env*",
    "*.config/claude-voice/machines/*",
    "*.config/dreame*",
    "*.config/tv-mcp*",
    "*secrets.yaml*",
    "*.config/gh/hosts.yml*",
    "*.claude/.credentials.json*",
)
#: What a secret looks like: tokens by their prefix, key headers, long hex or base64 runs,
#: and a secret's name set to a value. A 40-character hex run is a commit, and allowed.
SECRET_SHAPES = (
    re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|sk-[A-Za-z0-9_-]{16,})"),
    re.compile(r"\b(?:xox[abprs]-[A-Za-z0-9-]{10,}|AKIA[0-9A-Z]{16})"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"(?<![0-9a-fA-F])(?:[0-9a-fA-F]{32,39}|[0-9a-fA-F]{41,})(?![0-9a-fA-F])"),
    re.compile(r"(?=[A-Za-z0-9+/]*[0-9])(?=[A-Za-z0-9+/]*[A-Z])(?=[A-Za-z0-9+/]*[a-z])[A-Za-z0-9+/]{40,}={0,2}"),
    re.compile(r"(?i)\b(?:token|secret|password|passwd|api[_-]?key|client[_-]?secret|access[_-]?key)\s*[:=]\s*\S{6,}"),
)
SESSION_ID = re.compile(r"[0-9a-f][0-9a-f-]{7,63}")
ISSUE = re.compile(r"\bgh\s+issue\s+create\b")
ISSUE_VALUES = {
    "--repo": "repo",
    "-R": "repo",
    "--title": "title",
    "-t": "title",
    "--body": "body",
    "-b": "body",
    "--body-file": "body_file",
    "-F": "body_file",
    "--label": "label",
    "-l": "label",
}


def _tmp_root() -> Path:
    uid = getattr(os, "getuid", lambda: 0)()
    return Path(f"/tmp/claude-{uid}")


def _in_own_scratchpad(target: str, cwd: str, session_id: str, tmp_root: Path) -> bool:
    """Whether `target`, with every link and `..` followed, lies strictly inside
    <tmp root>/<project>/<this session>/scratchpad."""
    if not SESSION_ID.fullmatch(session_id):
        return False
    path = Path(os.path.realpath(os.path.join(cwd, target)))
    try:
        parts = path.relative_to(os.path.realpath(tmp_root)).parts
    except ValueError:
        return False
    return len(parts) >= 4 and parts[1] == session_id and parts[2] == "scratchpad"


def _recursive(word: str) -> bool:
    if word.startswith("--"):
        # RULE: every abbreviation of recursive counts as recursive
        return len(word) >= 3 and "--recursive".startswith(word)
    return word.startswith("-") and bool(re.fullmatch(r"-[a-zA-Z]*[rR][a-zA-Z]*", word))


FIND_RUNS = ("-exec", "-execdir", "-ok", "-okdir")


def _rm_removes_a_tree(raw: list[str], at: int) -> bool:
    """Whether the rm at raw[at] removes a tree, or cannot be read plainly enough to say."""
    word = raw[at]
    before = QUOTING.sub("", raw[at - 1]) if at else ";"
    if before in FIND_RUNS:
        return True
    if before == "git" and raw[at - 1] == "git":
        return False
    # RULE: a single file rm is exempt only with a plain command word and plain flags
    if word.rsplit("/", 1)[-1] != "rm" or BUILT.search(word):
        return True
    rest = raw[at + 1 :]
    rest = rest[: rest.index(";")] if ";" in rest else rest
    flags = [w for w in rest if QUOTING.sub("", w).startswith(("-", "$"))]
    return any(BUILT.search(w) or QUOTING.search(w) or _recursive(QUOTING.sub("", w)) for w in flags)


def removes_a_tree(command: str) -> bool:
    """Whether the command, read after quotes and backslashes are taken away, removes a
    folder with everything in it: rm with a recursive option however rm is reached, a
    remove that find runs, or a tree removal inside another program. A single-file rm
    and `git rm` are not, and keep the rules they always had."""
    flat = QUOTING.sub("", command)
    if "rmtree" in flat or re.search(r"(?:^|\s)-delete(?=\s|$)", flat):
        return True
    raw = SEPARATORS.sub(" ; ", command).split()
    found = [at for at, word in enumerate(raw) if QUOTING.sub("", word).rsplit("/", 1)[-1] == "rm"]
    return any(_rm_removes_a_tree(raw, at) for at in found)


def _removal(command: str, cwd: str, session_id: str, tmp_root: Path) -> str | None:
    # RULE: any recursive remove asks however it is spelled
    if not removes_a_tree(command):
        return None
    # RULE: a recursive remove is let through only inside the sessions own scratchpad
    if QUOTING.search(command) or CONTROL.search(command) or EXPANDS.search(command):
        return ASK
    targets = _remove_targets(command.split())  # no quoting is left: this is how the shell splits it
    if targets and all(_in_own_scratchpad(t, cwd, session_id, tmp_root) for t in targets):
        return ALLOW
    return ASK


def _remove_targets(words: list[str]) -> list[str]:
    """The paths a plain `rm` removes, or none when it is not one or has an unknown option."""
    if not words or words[0] != "rm":
        return []
    targets, options_done = [], False
    for word in words[1:]:
        if not options_done and word == "--":
            options_done = True
        elif not options_done and word.startswith("-"):
            if not REMOVE_OPTIONS.fullmatch(word):
                return []
        else:
            targets.append(word)
    return targets


def _repo_name(text: str) -> str:
    text = re.sub(r"^(?:https?://|git@)?(?:www\.)?github\.com[/:]", "", text.strip())
    return re.sub(r"\.git/?$", "", text).strip("/").lower()


def _names_a_term(terms: Path | None, texts: Sequence[str], files: Sequence[Path]) -> bool:
    """Whether any text or file holds one of the terms. Only grep's count is read, so the
    terms never reach this process's output. Anything it cannot check counts as a hit."""
    if terms is None or not terms.is_file() or not os.access(terms, os.R_OK):
        return True
    base = ["grep", "-c", "-i", "-F", "-f", str(terms)]
    try:
        for given, data in [([], "\n".join(texts)), *[([str(f)], None) for f in files]]:
            out = subprocess.run(
                [*base, "--", *given], input=data, capture_output=True, text=True, timeout=10, check=False
            )
            if out.returncode != 1 or out.stdout.strip() != "0":
                return True
    except (OSError, subprocess.SubprocessError):
        return True
    return False


def _tracked(file: Path) -> bool:
    """Whether git tracks this file in the repository around it: never an ignored or loose one."""
    try:
        out = safe_git.run(
            "-C",
            str(file.parent),
            "ls-files",
            "--error-unmatch",
            "--",
            file.name,
            capture_output=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return out.returncode == 0


def _body_file_may_be_read(file: Path, session_id: str, tmp_root: Path) -> bool:
    real = Path(os.path.realpath(file))
    if any(fnmatch.fnmatchcase(str(real), pattern) for pattern in SECRET_FILES) or ".git" in real.parts:
        return False
    return _in_own_scratchpad(str(real), "/", session_id, tmp_root) or (real.is_file() and _tracked(real))


def _holds_a_secret(texts: Sequence[str], files: Sequence[Path]) -> bool:
    try:
        whole = "\n".join([*texts, *(f.read_text(encoding="utf-8", errors="replace") for f in files)])
    except OSError:
        return True
    return any(shape.search(whole) for shape in SECRET_SHAPES)


def _issue(
    command: str, cwd: str, repos: Sequence[str], terms: Path | None, session_id: str = "", tmp_root: Path = Path("/")
) -> str | None:
    if not ISSUE.search(command):
        return None
    # RULE: an issue is filed without asking only in the programmes own repositories
    if CONTROL.search(command):
        return ASK
    # RULE: an issue that names a variable or a home folder is asked about
    if "$" in command or "~" in command:
        return ASK
    found = _issue_values(shlex.split(command))
    if found is None or _repo_name(found.get("repo", "")) not in {_repo_name(r) for r in repos}:
        return ASK
    files = [Path(cwd, found["body_file"])] if "body_file" in found else []
    return _issue_text(found, files, terms, session_id, tmp_root)


def _issue_values(words: list[str]) -> dict[str, str] | None:
    """The values of a plain `gh issue create` with a title and one body, or None."""
    if words[:3] != ["gh", "issue", "create"]:
        return None
    found: dict[str, str] = {}
    rest = words[3:]
    while rest:
        word = rest.pop(0)
        flag, _, value = word.partition("=") if word.startswith("--") else (word, "", "")
        key = ISSUE_VALUES.get(flag)
        if key is None or (not value and not rest):
            return None
        found[key] = value or rest.pop(0)
    one_body = ("body" in found) != ("body_file" in found)
    if "title" not in found or not one_body or found.get("body_file") == "-":
        return None
    return found


def _issue_text(found: dict[str, str], files: list[Path], terms: Path | None, session_id: str, tmp_root: Path) -> str:
    """allow when what the issue would publish is safe to publish, else ask."""
    # RULE: an issue whose body file is neither tracked nor in the scratchpad is asked about
    if not all(_body_file_may_be_read(f, session_id, tmp_root) for f in files):
        return ASK
    texts = [found["title"], found.get("body", ""), found.get("label", "")]
    # RULE: an issue that holds a secret shape is asked about
    if _holds_a_secret(texts, files):
        return ASK
    # RULE: an issue that names a personal term is asked about
    if _names_a_term(terms, texts, files):
        return ASK
    return ALLOW


#: git settings whose value git runs as code, set from any command (runs-as-owner v1.4 §3).
RUNS_CODE = re.compile(
    r"(core\.(hookspath|fsmonitor|sshcommand|pager|editor|askpass|gitproxy)|sequence\.editor"
    r"|gpg\.program|gpg\..+\.program|(credential|alias|include|includeif|filter)\..+"
    r"|diff\.external|diff\..+\.(textconv|command)|(difftool|mergetool)\..+\.cmd|merge\..+\.driver"
    r"|remote\..+\.(uploadpack|receivepack))",
    re.IGNORECASE,
)
#: The one hooks folder a session may point git at: the tracked, reviewed one.
TRACKED_HOOKS = ".githooks"
#: git config options that read rather than set.
CONFIG_READS = frozenset({"--get", "--get-all", "--get-regexp", "--get-urlmatch", "--list", "-l", "--unset"})


def _sets_code(key: str, value: str | None) -> bool:
    if not RUNS_CODE.fullmatch(key) or value is None:
        return False
    # RULE: the session hook lets the contributing setup line through
    return not (key.lower() == "core.hookspath" and value == TRACKED_HOOKS)


def _config_sets_code(words: list[str]) -> bool:
    """Whether `git config ...` sets a key whose value runs."""
    rest = words[words.index("config") + 1 :]
    if CONFIG_READS.intersection(rest):
        return False
    plain, i = [], 0
    while i < len(rest):
        if rest[i] in ("-f", "--file", "--blob"):
            i += 2
            continue
        if not rest[i].startswith("-"):
            plain.append(rest[i])
        i += 1
    return bool(plain) and _sets_code(plain[0], plain[1] if len(plain) > 1 else None)


def _from_environment(words: list[str]) -> list[str]:
    """The keys `git --config-env=KEY=VAR` sets. Their value is unknown here, so even the
    tracked hooks folder can't be told apart from any other."""
    given = [w.removeprefix("--config-env=") for w in words if w.startswith("--config-env=")]
    given += [words[i + 1] for i, w in enumerate(words[:-1]) if w == "--config-env"]
    return [g.partition("=")[0] for g in given]


def _git_sets_code(words: list[str]) -> bool:
    """Whether one git command sets a setting git runs: with -c, from the environment, or by config."""
    overrides = [words[i + 1] for i, w in enumerate(words[:-1]) if w == "-c"]
    if any(_sets_code(*o.partition("=")[::2]) for o in overrides if "=" in o):
        return True
    # RULE: a setting taken from the environment is refused for every key that runs code
    if any(RUNS_CODE.fullmatch(key) for key in _from_environment(words)):
        return True
    return "config" in words and _config_sets_code(words)


def runs_code(command: str) -> bool:
    """Whether any git command in `command` sets, for good or for one run, a setting git runs."""
    for part in SEPARATORS.split(command):
        try:
            words = shlex.split(part)
        except ValueError:  # unbalanced quotes: judged on the raw text, never asked about
            words = part.split()
        if words and os.path.basename(words[0]) == "git" and _git_sets_code(words):
            return True
    return False


def _code_setting(command: str) -> str | None:
    # RULE: the session hook refuses any hooks path but the tracked folder
    return DENY if runs_code(command) else None


def decision(hook_input: dict[str, Any], *, tmp_root: Path, repos: Sequence[str], terms: Path | None) -> str | None:
    """allow, ask, deny, or None to leave the command to Claude Code's own rules."""
    if hook_input.get("tool_name") != "Bash":
        return None
    command = str((hook_input.get("tool_input") or {}).get("command", ""))
    cwd = str(hook_input.get("cwd") or "/")
    session_id = str(hook_input.get("session_id") or "")
    for judge in (
        lambda: _code_setting(command),
        lambda: _removal(command, cwd, session_id, tmp_root),
        lambda: _issue(command, cwd, repos, terms, session_id, tmp_root),
    ):
        try:
            said = judge()
        except ValueError:  # shlex could not read it
            said = ASK
        if said:
            return said
    return None


REASONS = {
    ALLOW: "a routine step the owner allowed: inside this session's own scratchpad, or an issue in its own programme",
    ASK: "only a remove inside this session's own scratchpad, or a clean issue in its own programme, runs unasked",
    DENY: "a git setting whose value runs as the owner later; only core.hooksPath .githooks may be set",
}


def answer(hook_input: dict[str, Any], *, tmp_root: Path, repos: Sequence[str], terms: Path | None) -> dict[str, Any]:
    """Claude Code's hook answer. A hook that fails while judging a remove or an issue asks."""
    try:
        said = decision(hook_input, tmp_root=tmp_root, repos=repos, terms=terms)
    except Exception:  # noqa: BLE001 - whatever went wrong, the owner is asked
        command = str((hook_input.get("tool_input") or {}).get("command", ""))
        # RULE: a hook that cannot decide asks
        said = ASK if removes_a_tree(command) or ISSUE.search(command) else None
    if said is None:
        return {}
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": said,
            "permissionDecisionReason": REASONS[said],
        }
    }


SAMPLE = {"tool_name": "Bash", "session_id": "0" * 8, "cwd": "/", "tool_input": {"command": "rm -r /nowhere"}}


def self_check(command: Sequence[str], run: Callable[..., Any] = subprocess.run) -> dict[str, Any]:
    """Run the hook once, as Claude Code would, and say whether it answers. A hook that
    cannot start leaves every recursive remove inside a repository unasked."""
    try:
        out = run(list(command), input=json.dumps(SAMPLE), capture_output=True, text=True, timeout=10, check=False)
        said = json.loads(out.stdout)["hookSpecificOutput"]["permissionDecision"]
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError):
        said = None
    # RULE: a hook that does not answer is shown as an error with the fix
    ok = said == ASK
    return {
        "id": "session_hook",
        "title": "The session hook answers",
        "ok": ok,
        "required": False,
        "affects": "the owner's question before a session removes a folder in a repository",
        "detail": "the session hook asked about a sample remove, as it should"
        if ok
        else "the session hook did not answer, so a session could remove folders in a repository unasked",
        "fix": "" if ok else "Run `claude-voice permissions` to write the hook again, and check that python3 runs",
        "fix_url": None,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="session_hook.py")
    parser.add_argument("--repos", default="", help="owner/name of each repository of the session's programme")
    parser.add_argument("--terms", type=Path, default=None, help="the owner's personal terms, one per line")
    parser.add_argument("--tmp-root", type=Path, default=None, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        hook_input = json.load(sys.stdin)
    except ValueError:
        hook_input = {"tool_name": "Bash", "tool_input": {"command": "rm -r"}}
    repos = [r for r in args.repos.split(",") if r]
    out = answer(hook_input, tmp_root=args.tmp_root or _tmp_root(), repos=repos, terms=args.terms)
    if out:
        sys.stdout.write(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
