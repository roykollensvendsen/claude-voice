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
`python -c`, one that writes what a file or a commit says (git apply, am, checkout, merge,
pull and stash, patch, tar x and unzip), and a plain variable other than HOME. The lane
users close that edge. The memory folder under ~/.claude/projects stays writable on purpose.

It runs inside the session hook, so it uses the standard library only.
"""

from __future__ import annotations

import functools
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
#: `${X:-default}` and the like, whose value the hook can't know: `${X:-.cla}ude` is `.claude`.
PARAMETER = re.compile(r"\$\{[^}]*[:#%/^,@!+=?-]")
#: Bash's brace expansion: `{a,b}`, and `{1..9}` or `{a..z}` with an optional step.
BRACE = re.compile(r"\{([^{}]*)\}")
SEQUENCE = re.compile(r"(-?\d+)\.\.(-?\d+)(?:\.\.(-?\d+))?|([A-Za-z])\.\.([A-Za-z])(?:\.\.(-?\d+))?")
#: The most words one brace may make before the word counts as locked.
BRACE_LIMIT = 256
#: The folders under ~/.claude whose links point to where the steering file really lives.
STEERING_FOLDERS = ("skills", "hooks", "agents", "commands", "rules", "output-styles", "plugins")
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
    lexer.commenters = ""  # a `#` inside a word, as in `${#X}`, is no comment to bash
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
    if PARAMETER.search(word):
        # RULE: a parameter with a default or any other operator counts as locked
        return LOCKED
    try:
        spellings = _braces(word)
    except ValueError:
        # RULE: a brace the hook cannot expand counts as locked
        return LOCKED
    return max(_level_of_spelling(w, cwd, home) for w in spellings)


def _level_of_spelling(word: str, cwd: str, home: str) -> int:
    if EXPANDS.search(word):
        # A glob that matches nothing is safe: bash leaves it as written, and that is judged by its names.
        # RULE: a word the shell would change is judged by every name it can reach
        found = glob.glob(os.path.join(cwd, word))
        named = max((level for marker, level in MARKERS if marker in word), default=NONE)
        return max([named] + [_level_of_path(p, home) for p in found])
    try:
        written = os.path.normpath(os.path.join(cwd, word))
        resolved = os.path.realpath(os.path.join(cwd, word))
    except (OSError, ValueError):
        # RULE: a word that cannot be resolved counts as locked
        return LOCKED  # fail closed
    # RULE: a path is judged both as written and as resolved
    return max(_level_of_path(written, home), _level_of_path(resolved, home))


def _braces(word: str) -> list[str]:
    """Every word bash's brace expansion makes of `word`. Raises ValueError past BRACE_LIMIT."""
    done, todo = [], [word]
    while todo:
        text = todo.pop()
        match = next((m for m in BRACE.finditer(text) if _expands(m.group(1))), None)
        if match is None:
            done.append(text)
            continue
        todo += [text[: match.start()] + part + text[match.end() :] for part in _alternatives(match.group(1))]
        if len(done) + len(todo) > BRACE_LIMIT:
            raise ValueError("too many words")
    return done


def _expands(inner: str) -> bool:
    return "," in inner or SEQUENCE.fullmatch(inner) is not None


def _alternatives(inner: str) -> list[str]:
    found = SEQUENCE.fullmatch(inner)
    if found is None:
        return inner.split(",")
    first, last, step = found.group(1, 2, 3)
    if first is None:
        first, last, step = found.group(4, 5, 6)
        return [chr(n) for n in _steps(ord(first), ord(last), step)]
    return [str(n) for n in _steps(int(first), int(last), step)]


def _steps(first: int, last: int, step: str | None) -> range:
    size = abs(int(step or 1)) or 1
    if abs(last - first) // size > BRACE_LIMIT:
        raise ValueError("too long a sequence")
    way = 1 if last >= first else -1
    return range(first, last + way, size * way)


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
    if any(path == target or path.startswith(target + os.sep) for target in _linked_out(at_home)):
        # RULE: what a link under claude points to is refused too
        return True
    # RULE: every steering file at home is refused
    return path.startswith(at_home + os.sep) and _parts(path[len(at_home) :])[0] in LOCKED_AT_HOME


@functools.lru_cache(maxsize=8)
def _linked_out(at_home: str) -> tuple[str, ...]:
    """Where each steering folder under ~/.claude, and each link right inside one, really is.
    A skill linked in from elsewhere steers from there, so writing there steers too."""
    found = []
    for name in STEERING_FOLDERS:
        folder = os.path.join(at_home, name)
        found.append(os.path.realpath(folder))
        try:
            found += [os.path.realpath(e.path) for e in os.scandir(folder) if e.is_symlink()]
        except OSError:  # no such folder
            continue
    return tuple(found)


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
