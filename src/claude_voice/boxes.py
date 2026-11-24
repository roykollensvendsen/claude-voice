"""Why each permission box reached the owner, one line per box in the bridge's own log.

A box is the question Claude Code asks before a tool runs. The bridge labels each one as it
arrives, from the phone hook or from a voice session, with the cause it can name in code:

- the folder's ask rule that matches the call;
- the session hook, which asks on its own for some shell commands;
- a session not in auto mode, where Claude Code asks for anything no rule allows;
- no rule at all, so auto mode's classifier asked, or its pause after too many blocks;
- a deny rule that matches, which should never make a box, so it is logged as a warning too.

The line is advice for the owner and for the nightly count, never what is pressed: an answer
is always read from the box itself. It names a command by its first words only, and every
text from the box is shown as data, on one line, cut short. The log stays on this computer
(``~/.local/state/claude-voice/boxes.jsonl``) and never reaches GitHub.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shlex
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, tzinfo
from pathlib import Path
from typing import Any

from . import session_hook

log = logging.getLogger(__name__)

LOG = Path("~/.local/state/claude-voice/boxes.jsonl")
HOOK_ASKED = "session hook asked"
NOT_AUTO = "not in auto mode"
NO_RULE = "no rule matched (classifier or its pause)"
ASK_RULE = "ask rule: "
DENY_RULE = "a deny matched: "
SHOWN = 200  # characters of box text a line keeps
PREFIX_WORDS = 2  # words of a command a line keeps
EDITORS = frozenset({"Edit", "Write", "MultiEdit", "NotebookEdit"})
READERS = frozenset({"Read", "Grep", "Glob"})
CONTROL = re.compile(r"[\x00-\x1f\x7f]+")
RULE = re.compile(r"^([A-Za-z0-9_-]+)(?:\((.*)\))?$", re.DOTALL)
JOINS = re.compile(r"\s*(?:&&|\|\||;|\|)\s*")


def shown(text: str) -> str:
    """Text from a box as data: control characters made spaces, on one line, cut short."""
    flat = CONTROL.sub(" ", text).strip()
    # RULE: box text is shown on one line and cut short
    return flat if len(flat) <= SHOWN else flat[: SHOWN - 1] + "…"


@dataclass(frozen=True)
class Rules:
    """The allow, ask and deny rules in effect in one folder, and its session hook's command."""

    allow: tuple[str, ...] = ()
    ask: tuple[str, ...] = ()
    deny: tuple[str, ...] = ()
    hook: str | None = None


def _read(file: Path) -> dict[str, Any]:
    try:
        data = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def project_root(cwd: str, home: Path) -> Path | None:
    """The nearest folder at or above `cwd` that holds Claude Code settings or a git checkout."""
    if not cwd:
        return None
    folder = Path(cwd)
    for place in (folder, *folder.parents):
        if place == home:
            return None
        if (place / ".claude").is_dir() or (place / ".git").exists():
            return place
    return None


def rules_for(cwd: str, home: Path) -> Rules:
    """The rules Claude Code reads for a session in `cwd`: the owner's own settings, then the
    project's shared and local ones, with the session hook from whichever names it."""
    root = project_root(cwd, home)
    files = [home / ".claude" / "settings.json"]
    if root is not None:
        files += [root / ".claude" / "settings.json", root / ".claude" / "settings.local.json"]
    found: dict[str, list[str]] = {"allow": [], "ask": [], "deny": []}
    hook = None
    for settings in map(_read, files):
        permissions = settings.get("permissions") or {}
        for key, rules in found.items():
            rules += [str(r) for r in permissions.get(key) or [] if isinstance(r, str)]
        hook = _hook(settings) or hook
    return Rules(tuple(found["allow"]), tuple(found["ask"]), tuple(found["deny"]), hook)


def _hook(settings: dict[str, Any]) -> str | None:
    for entry in (settings.get("hooks") or {}).get("PreToolUse") or []:
        for hook in entry.get("hooks") or [] if isinstance(entry, dict) else []:
            if isinstance(hook, dict) and "session_hook.py" in str(hook.get("command", "")):
                return str(hook["command"])
    return None


def _wildcard(pattern: str, star: str) -> re.Pattern[str]:
    """A rule's pattern as a regular expression: `**` any text, `*` the given part, `?` one character."""
    parts = re.split(r"(\*\*/?|\*|\?)", pattern)
    words = {"**": ".*", "**/": "(?:.*/)?", "*": star, "?": "." if star == ".*" else "[^/]"}
    return re.compile("".join(words.get(p, re.escape(p)) for p in parts), re.DOTALL)


def _bash_matches(spec: str, command: str) -> bool:
    """Claude Code's Bash rule: `*` any text, spaces too; `x:*` and `x *` also match `x` alone."""
    if spec.endswith(":*"):
        spec = spec[:-2] + " *"
    bare = spec[:-2] if spec.endswith(" *") else None
    pieces = [command.strip(), *JOINS.split(command.strip())]
    regex = _wildcard(spec, ".*")
    # RULE: a bash rule matches the whole command or any part of a chain
    return any(regex.fullmatch(p) or p == bare for p in pieces if p)


def _path_matches(spec: str, path: str, cwd: str, root: Path | None, home: Path) -> bool:
    """Claude Code's path rule: `//` from the root, `~/` from home, `/` from the project, else from cwd."""
    if not path:
        return False
    if spec.startswith("//"):
        full = spec[1:]
    elif spec.startswith("~/"):
        full = str(home) + spec[1:]
    elif spec.startswith("/"):
        full = str(root or cwd) + spec
    elif "/" not in spec.rstrip("/"):
        full = "**/" + spec  # a bare name matches at any depth, as in .gitignore
    else:
        full = os.path.join(cwd, spec.removeprefix("./"))
    target = os.path.normpath(os.path.join(cwd, path)).replace("\\", "/")  # one separator on Windows too
    return bool(_wildcard(full.replace("\\", "/"), "[^/]*").fullmatch(target))


@dataclass(frozen=True)
class Box:
    """One box as the triager sees it."""

    tool: str
    tool_input: Any
    cwd: str
    mode: str


def matches(rule: str, box: Box, home: Path) -> bool:
    """Whether one permission rule covers this call."""
    found = RULE.match(rule.strip())
    if found is None:
        return False
    name, spec = found.group(1), found.group(2)
    if name.startswith("mcp__"):
        return box.tool == name or box.tool.startswith(name + "__")
    if spec is None:
        return box.tool == name
    if name == "Bash":
        given = box.tool_input if isinstance(box.tool_input, dict) else {}
        return box.tool == "Bash" and _bash_matches(spec, str(given.get("command", "")))
    return _file_rule_matches(name, spec, box, home)


def _file_rule_matches(name: str, spec: str, box: Box, home: Path) -> bool:
    # RULE: an edit rule covers every tool that writes a file
    if (name == "Edit" and box.tool in EDITORS) or (name == "Read" and box.tool in READERS) or name == box.tool:
        return _path_matches(spec, _file(box), box.cwd, project_root(box.cwd, home), home)
    return False


def _file(box: Box) -> str:
    given = box.tool_input if isinstance(box.tool_input, dict) else {}
    return str(given.get("file_path") or given.get("notebook_path") or given.get("path") or "")


def _first(rules: Iterable[str], box: Box, home: Path) -> str | None:
    return next((r for r in rules if matches(r, box, home)), None)


def _hook_asks(box: Box, hook: str | None) -> bool:
    """Whether the folder's session hook would ask for this shell command."""
    if box.tool != "Bash" or hook is None:
        return False
    try:
        words = shlex.split(hook)
    except ValueError:
        return False
    repos = _after(words, "--repos")
    terms = _after(words, "--terms")
    hook_input = {"tool_name": "Bash", "tool_input": box.tool_input, "cwd": box.cwd}
    said = session_hook.decision(
        hook_input,
        tmp_root=session_hook._tmp_root(),
        repos=[r for r in (repos or "").split(",") if r],
        terms=Path(terms) if terms else None,
    )
    return said == session_hook.ASK


def _after(words: list[str], flag: str) -> str | None:
    return words[words.index(flag) + 1] if flag in words[:-1] else None


def cause(box: Box, rules: Rules, home: Path) -> str:
    """Why this box reached the owner, as far as code can tell."""
    denied = _first(rules.deny, box, home)
    if denied is not None:
        # RULE: a box that a deny rule matches is logged as a warning
        log.warning("a box reached the owner although a deny rule matches: %s", shown(denied))
        return DENY_RULE + shown(denied)
    asked = _first(rules.ask, box, home)
    if asked is not None:
        # RULE: a box an ask rule matches names that rule
        return ASK_RULE + shown(asked)
    if _hook_asks(box, rules.hook):
        return HOOK_ASKED
    # RULE: a box from a session not in auto mode says so
    if box.mode != "auto":
        return NOT_AUTO
    return NO_RULE


def named(box: Box) -> str:
    """What the box is about, without its whole text: a command's first words, or a file."""
    if box.tool == "Bash":
        given = box.tool_input if isinstance(box.tool_input, dict) else {}
        # RULE: a line names a command by its first words only
        return shown(" ".join(str(given.get("command", "")).split()[:PREFIX_WORDS]))
    return shown(_file(box))


#: For each cause, the one removal the report proposes. It only proposes: it writes nothing.
PROPOSALS = {
    ASK_RULE: "weigh this ask rule in the ask cuts (deny, remove or keep), with these counts",
    HOOK_ASKED: "a change in the session's habit, or in the session hook by a pull request",
    NOT_AUTO: "start this session in auto mode",
    NO_RULE: "an allow row for the shipped table, by a pull request, if a careful reviewer would just say yes",
    DENY_RULE: "a bug to report: a deny should never make a box",
}


def _proposal(cause: str) -> str:
    return next((text for start, text in PROPOSALS.items() if cause.startswith(start)), "none: an unknown cause")


def _day_lines(file: Path, day: date, tz: tzinfo | None) -> list[dict[str, Any]]:
    try:
        text = file.expanduser().read_text(encoding="utf-8")
    except OSError:
        return []
    found = []
    for raw in text.splitlines():
        try:
            line = json.loads(raw)
            when = datetime.fromisoformat(str(line["at"])).astimezone(tz).date()
        except (ValueError, KeyError, TypeError):
            continue
        if when == day:
            found.append(line)
    return found


def report(file: Path, day: date, tz: tzinfo | None = None) -> str:
    """The day's boxes, grouped by session, tool, what and cause, with counts and one proposal
    each, the biggest group first. Plain text for the manager; it changes nothing."""
    groups = Counter(
        tuple(shown(str(line.get(key, ""))) for key in ("session", "tool", "what", "cause"))
        for line in _day_lines(file, day, tz)
    )
    if not groups:
        return f"No boxes reached the owner on {day.isoformat()}.\n"
    out = [f"{sum(groups.values())} boxes reached the owner on {day.isoformat()}, in {len(groups)} groups:"]
    for (session, tool, what, cause), count in sorted(groups.items(), key=lambda g: (-g[1], g[0])):
        out.append(f"- {count} × {session} · {tool} · {what or '-'} · {cause}")
        # RULE: the report proposes one removal for each group and writes nothing
        out.append(f"  proposal: {_proposal(cause)}")
    return "\n".join(out) + "\n"


class Triager:
    """Labels each box with its cause and keeps one line for it in the log."""

    def __init__(
        self,
        file: Path = LOG,
        home: Path | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.file = file.expanduser()
        self.home = home or Path.home()
        self.now = now

    def label(self, prompt: str, session: str, box: Box) -> str:
        """The box's cause, with its line written to the log."""
        why = cause(box, rules_for(box.cwd, self.home), self.home)
        line = {
            "at": self.now().isoformat(timespec="seconds"),
            "prompt": prompt,
            "session": shown(session),
            "tool": shown(box.tool),
            "what": named(box),
            "mode": shown(box.mode),
            "cause": why,
        }
        self.file.parent.mkdir(parents=True, exist_ok=True)
        with os.fdopen(os.open(self.file, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600), "a", encoding="utf-8") as out:
            out.write(json.dumps(line, ensure_ascii=False) + "\n")
        return why

    def causes(self) -> dict[str, str]:
        """Each logged box's cause, by its prompt id."""
        try:
            text = self.file.read_text(encoding="utf-8")
        except OSError:
            return {}
        found = {}
        for raw in text.splitlines():
            try:
                line = json.loads(raw)
            except ValueError:
                continue
            if isinstance(line, dict) and isinstance(line.get("prompt"), str):
                found[line["prompt"]] = str(line.get("cause", ""))
        return found
