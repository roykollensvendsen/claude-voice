"""The files that steer the next session, judged in a shell command (approver v4.1 b1, f6, f7).

Claude Code loads hooks, skills, agents, commands, rules, output styles, the MCP list and
the instruction files into the next session. A session that writes one steers the next
session, before anyone has seen it. The deny rules in permissions.json catch the Edit and
Write tools and the plain shell spellings. This catches every other shell spelling: each
word that names a path is resolved, `..` and links followed, and judged.

- Under a file that steers and is not tracked (hooks, agents, commands, rules, output
  styles, settings, `.mcp.json`, `CLAUDE.local.md`), and anything under `~/.claude` that
  steers: refused.
- Under a tracked one, which a pull request reviews later (skills, `CLAUDE.md`,
  `.claude/CLAUDE.md`, `AGENTS.md`): asked.
- A plain read passes: one reader alone, with no chain, redirection, substitution or
  wrapper, and none of the reader's options that write or run.

What a text rule cannot see stays open: a program that builds a path itself, such as
`python -c`, and a variable other than HOME. The lane users close that edge. The memory
folder under ~/.claude/projects stays writable on purpose.

It runs inside the session hook, so it uses the standard library only.
"""

from __future__ import annotations

import glob
import os
import re
import shlex

NONE, ASKED, LOCKED = 0, 1, 2
#: Under any `.claude` folder: what Claude Code loads, and which of it is tracked and reviewed.
LOCKED_IN_CLAUDE = frozenset({"hooks", "agents", "commands", "rules", "output-styles"})
LOCKED_IN_CLAUDE |= {"settings.json", "settings.local.json"}
ASKED_IN_CLAUDE = frozenset({"skills", "CLAUDE.md"})
#: Under ~/.claude, nothing is reviewed, so every one of them is refused.
LOCKED_AT_HOME = LOCKED_IN_CLAUDE | ASKED_IN_CLAUDE | {"plugins"}
LOCKED_NAMES = frozenset({".mcp.json", "CLAUDE.local.md"})
ASKED_NAMES = frozenset({"CLAUDE.md", "AGENTS.md"})
#: Text in a word the shell would still change: judged by the names it holds.
MARKERS: tuple[tuple[str, int], ...] = ((".claude", LOCKED), (".mcp.json", LOCKED), ("CLAUDE.local.md", LOCKED))
MARKERS += (("CLAUDE.md", ASKED), ("AGENTS.md", ASKED))
EXPANDS = re.compile(r"[*?\[\]{}$`]")
#: Programs that only read the files they are given.
READERS = frozenset({"cat", "head", "tail", "wc", "grep", "rg", "ls", "stat"})
GIT_READS = frozenset({"diff", "log", "show", "status", "blame"})
#: A reader's options that write a file or run a program.
WRITES_OR_RUNS = ("--output", "--ext-diff", "--textconv", "--pre")
#: Anything that chains, pipes, redirects or substitutes: never a plain read.
NOT_PLAIN = re.compile(r"[;&|<>`\n\r]|\$\(")
#: Programs that run a command given as one word.
SHELLS = frozenset({"sh", "bash", "zsh", "dash", "ksh", "fish", "eval", "su", "sudo", "ssh"})


def judge(command: str, cwd: str, home: str) -> str | None:
    """deny, ask, or None when the command names nothing that steers, or only reads it.
    Raises ValueError when shlex cannot read the command."""
    level = _level_of_command(command, cwd, home)
    if level == NONE or _plain_read(command):
        return None
    # RULE: a shell write to what steers the next session is refused
    return "deny" if level == LOCKED else "ask"


def _tokens(command: str) -> list[str]:
    lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    return [piece for token in lexer for piece in token.split("`") if piece]


def _level_of_command(command: str, cwd: str, home: str) -> int:
    tokens = _tokens(command)
    level = max((_level_of_word(t, cwd, home) for t in tokens), default=NONE)
    if any(os.path.basename(t) in SHELLS for t in tokens):
        # RULE: a command handed to a shell as one word is judged too
        level = max([level] + [_level_of_command(t, cwd, home) for t in tokens if " " in t])
    return level


def _level_of_word(word: str, cwd: str, home: str) -> int:
    words = [word] + ([word.split("=", 1)[1]] if "=" in word else [])
    return max(_level_of_text(w, cwd, home) for w in words)


def _level_of_text(word: str, cwd: str, home: str) -> int:
    word = _home_spelled_out(word, home)
    if EXPANDS.search(word):
        # RULE: a word the shell would change is judged by every name it can reach
        found = glob.glob(os.path.join(cwd, word))
        named = max((level for marker, level in MARKERS if marker in word), default=NONE)
        return max([named] + [_level_of_path(p, home) for p in found])
    try:
        path = os.path.realpath(os.path.join(cwd, word))
    except (OSError, ValueError):
        # RULE: a word that cannot be resolved counts as locked
        return LOCKED  # fail closed
    return _level_of_path(path, home)


def _home_spelled_out(word: str, home: str) -> str:
    if word == "~" or word.startswith("~/"):
        word = home + word[1:]
    return word.replace("${HOME}", home).replace("$HOME", home)


def _level_of_path(path: str, home: str) -> int:
    """How a resolved path is judged: at home, then in any folder."""
    if _at_home(path, home):
        return LOCKED
    parts = _parts(path)
    if parts and parts[-1] in LOCKED_NAMES:
        return LOCKED
    level = ASKED if parts and parts[-1] in ASKED_NAMES else NONE
    for i, part in enumerate(parts):
        after = parts[i + 1] if i + 1 < len(parts) else None
        if part == ".claude" and (after is None or after in LOCKED_IN_CLAUDE):
            return LOCKED
        if part == ".claude" and after in ASKED_IN_CLAUDE:
            # RULE: a shell write to a tracked file that steers is asked
            level = ASKED
    return level


def _at_home(path: str, home: str) -> bool:
    """Whether a resolved path is ~/.claude itself, ~/.claude.json, or steers from under ~/.claude."""
    at_home = os.path.join(os.path.realpath(home), ".claude")
    if path in (at_home, at_home + ".json"):
        return True
    # RULE: every steering file at home is refused
    return path.startswith(at_home + os.sep) and _parts(path[len(at_home) :])[0] in LOCKED_AT_HOME


def _parts(path: str) -> list[str]:
    return [p for p in re.split(r"[\\/]", path) if p]


def _plain_read(command: str) -> bool:
    """One reader on its own, with no option that writes or runs."""
    if NOT_PLAIN.search(command):
        return False
    words = shlex.split(command)
    if not words:
        return False
    program = os.path.basename(words[0])
    if program == "git":
        return _git_read(words[1:])
    # RULE: a plain read of a locked path passes
    return program in READERS and not any(w.startswith(WRITES_OR_RUNS) for w in words)


def _git_read(args: list[str]) -> bool:
    """git diff, log, show, status or blame, with only -C before it, and no option that writes or runs."""
    i = 0
    while i < len(args) and args[i] == "-C":
        i += 2
    if i >= len(args) or args[i] not in GIT_READS:
        return False
    # RULE: a reader's option that writes or runs is refused
    return not any(w.startswith(WRITES_OR_RUNS) or w == "-o" for w in args[i + 1 :])
