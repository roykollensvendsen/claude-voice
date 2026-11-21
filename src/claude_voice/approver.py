"""Which approval boxes the approver session may answer; every other box waits for the owner.

The approver is a session that answers other sessions' routine boxes, so the owner sees
fewer of them (designs/approver-v1). What it may answer is decided here, in code, and
never by its own judgement. It is a short allow-list, and anything else is the owner's:

- the box matched none of the owner's fixed lines (`routing.FIXED`), and its sort is done;
- its input was not cut;
- the tool is Read on one absolute path inside the session's own repo, worktree or
  scratchpad, with symlinks followed;
- or Edit or Write on such a path inside the repo, but never under `.git`,
  `.github/workflows` or any `.claude` folder, since those run later as the owner;
- or the tool is Bash running one plain command of literal words, from the session's
  folder. It starts with a reader (`ls`, `cat`, `head`, `tail`, `grep`, `wc`) or with a
  listed subcommand of `git`, `uv` or `npm`, or with `make`. No word holds a `:`, so no
  web address or other machine. Every word, and every value glued to a flag, read as a
  path from the session's folder, stays inside those folders.

The last rule is why `cat ~/.config/...` or a symlink to a secret never passes: a word is
judged as the file it would name, whatever the program does with it. `uv run`, `make` and
`npm` still run the repo's own code; the sandbox is the boundary there.

**On duty.** The approver works only while the owner has switched it on (`[approver] on`
in the config) and it has looked at its boxes within the last two minutes. Only then does
a box from a session the bridge launched as a role wait for it, even with the owner at the
laptop. A session started by voice, which the owner may then work in, keeps today's way. Its
answer counts once: the hook gives Claude Code a plain allow or deny, never an "always".
An answer that comes after the box fell back to the screen is refused as too late. Each
decision is one line in the approver's log, with a hash of the input but never its text.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from . import prompts, routing, session_hook

logger = logging.getLogger(__name__)

READERS = frozenset({"ls", "cat", "head", "tail", "grep", "wc"})
#: The subcommands each program may run (approver-v1 v1.3); `make` takes any target.
SUBCOMMANDS = {
    "git": routing.GIT_SHAPES,
    "uv": frozenset({"run", "sync"}),
    "npm": frozenset({"test", "run", "ci"}),
}
#: Options that make uv fetch and run code from outside the repo.
UV_FETCHES = ("--with", "--from", "--index", "--extra-index-url", "--find-links")
#: Options that make git run a command given on the line or set its config.
GIT_RUNS = ("-c", "-x", "--exec")
PYTHONS = frozenset({"python", "python3"})
FIRST_WORDS = READERS | SUBCOMMANDS.keys() | {"make"}


def why_owner(
    tool: str,
    given: Any,
    lines: Iterable[int] | None,
    place: routing.Place,
    *,
    cwd: Path,
    cut: bool,
) -> str | None:
    """None when the approver may answer this box; otherwise why it is the owner's.

    `lines` are the fixed-list lines the sort found, or None while it has not finished.
    """
    # RULE: a box not yet sorted or cut goes to the owner
    if lines is None:
        return "not sorted yet"
    # RULE: a box with an owner line goes to the owner
    if found := sorted(lines):
        return f"owner line {found[0]}"
    if cut:
        return "the input was cut"
    given = given if isinstance(given, dict) else {}
    roots = [os.path.realpath(root) for root in (place.repo, place.scratchpad) if root is not None]
    if tool in ("Read", "Edit", "Write"):
        return _file_tool(tool, given.get("file_path"), roots, place)
    if tool == "Bash":
        return _command(str(given.get("command", "")), roots, cwd)
    # RULE: any other tool goes to the owner
    return f"{tool} is not on the approver's list"


def _file_tool(tool: str, path: Any, roots: list[str], place: routing.Place) -> str | None:
    # RULE: a symlink out of the repo goes to the owner for a file tool
    if not isinstance(path, str) or not os.path.isabs(path) or not routing.inside(path, roots, Path("/")):
        return "a path outside the session's folders"
    # RULE: an edit of git, workflow or claude files goes to the owner
    if tool != "Read" and not _writable(path, place):
        return "a file that runs later as the owner"
    return None


def _writable(path: str, place: routing.Place) -> bool:
    """Inside the repo, and not under .git, .github/workflows or any .claude folder."""
    rel = Path(os.path.relpath(os.path.realpath(path), os.path.realpath(place.repo)))
    return routing._editable(path, place) and ".claude" not in rel.parts


def _command(command: str, roots: list[str], cwd: Path) -> str | None:
    parts = routing.split_command(command)
    # RULE: anything but one plain allowed command goes to the owner
    if parts is None or len(parts) != 1 or parts[0][0] not in FIRST_WORDS:
        return "not one plain command from the approver's list"
    if why := _program(parts[0][0], parts[0][1:], cwd):
        return why
    # RULE: a command run from outside the repo goes to the owner
    if not routing.inside(str(cwd), roots, Path("/")):
        return "run from outside the session's folders"
    for word in parts[0][1:]:
        # RULE: a command naming a web address or another machine goes to the owner
        if ":" in word:
            return "names another machine"
        # RULE: a read outside the sessions folders always goes to the owner
        if not all(routing.inside(path, roots, cwd) for path in _as_paths(word)):
            return "a path outside the session's folders"
    return None


def _program(program: str, rest: list[str], cwd: Path) -> str | None:
    """Why this program's own words are the owner's, or None."""
    # RULE: a subcommand off the approvers list goes to the owner
    if program in SUBCOMMANDS and (not rest or rest[0] not in SUBCOMMANDS[program]):
        return f"{' '.join([program, *rest[:1]])} is not on the approver's list"
    # RULE: git that reaches a named remote or runs a command goes to the owner
    if program == "git" and ((rest[0] == "fetch" and len(rest) > 1) or any(w.startswith(GIT_RUNS) for w in rest)):
        return "git that reaches a named remote or runs a command"
    if program == "uv":
        return _uv(rest, cwd)
    # RULE: make that sets a variable goes to the owner
    if program == "make" and any("=" in w for w in rest):
        return "make that sets a variable"
    return None


def _uv(rest: list[str], cwd: Path) -> str | None:
    """Why uv's own words are the owner's, or None."""
    # RULE: uv run that fetches other code goes to the owner
    if any(w.startswith(UV_FETCHES) for w in rest):
        return "uv that fetches code from outside the repo"
    # RULE: uv runs only the repos own checks or its own python files
    if not _uv_runs_a_check(rest, cwd):
        return "uv that runs a program off the approver's list"
    return None


def _uv_runs_a_check(rest: list[str], cwd: Path) -> bool:
    """Whether `uv run` starts pytest, ruff or mypy, or python on a .py file or with -m one of
    those; `uv sync` starts nothing. `uv run` would start any program on the path otherwise.

    The first word after uv's own options is the program, so an option's value fails closed."""
    words = [w for w in rest[1:] if not w.startswith("-")]
    if rest[0] == "sync":
        return not words
    if not words:
        return False
    program, after = words[0], rest[rest.index(words[0]) + 1 :]
    if program in routing.UV_TOOLS:
        return True
    if program in PYTHONS and after[:1] == ["-m"]:
        return after[1:2] != [] and after[1] in routing.UV_TOOLS
    return program in PYTHONS and bool(after) and after[0].endswith(".py") and (cwd / after[0]).is_file()


def _as_paths(word: str) -> list[str]:
    """Every file `word` could name: itself, a long flag's value, or any tail of a short-flag
    cluster, since its value may follow any of its letters (`-rf..`, `-rf/etc/x`, `-C..`)."""
    if word.startswith("--"):
        return [word.split("=", 1)[1]] if "=" in word else []
    if word.startswith("-"):
        # RULE: a value glued to a short flag is judged as a path
        return [word[i:] for i in range(2, len(word))]
    return [word]


# -- on duty ------------------------------------------------------------------------------

CHECK_IN = 120.0  # seconds after its last look that the approver still counts as on duty
LOG = Path("~/.local/state/claude-voice/approver/log.jsonl")
TOO_LATE = "too late: the box is on the screen"
OFF = "the approver is switched off; the owner turns it on in the bridge's config"
PROOF_SECONDS = 5.0  # how long a hook's report of an approver call stays good
MAX_REPORTS = 32  # reports kept at once; a flood pushes out only the oldest
NOT_PROVEN = "only the approver session may call this, and its own hook did not report this call"
OWN_BOX = "the approver never answers its own box"


class Duty:
    """Whether the approver is on duty, who it is, and its log of decisions.

    `role` says whether a session was launched as a role, and `approver` whether it is the
    approver. A call to the approver's tools counts only with a fresh report from that
    session's own hook, naming the same tool and arguments, and each report counts once."""

    def __init__(
        self,
        on: bool,
        role: Callable[[str], bool] = lambda session: False,
        approver: Callable[[str], bool] = lambda session: False,
        clock: Callable[[], float] = time.monotonic,
        log: Path = LOG,
    ) -> None:
        self.on, self.role, self.approver, self.clock, self.log = on, role, approver, clock, log.expanduser()
        self.seen: float | None = None  # when the approver last looked at its boxes
        self.reports: list[tuple[float, str, str, str]] = []  # (at, session, tool, args hash)

    def report(self, session: str, tool: str, digest: str) -> None:
        """A session's hook says that session is about to call `tool` with these arguments."""
        self.reports.append((self.clock(), session, tool, digest))
        # RULE: the bridge keeps only the newest reports
        del self.reports[:-MAX_REPORTS]

    def caller(self, tool: str, digest: str) -> str | None:
        """The approver session calling `tool` with these arguments, or None when no fresh
        report from it says so. Only the approver's own matching report is used up."""
        now = self.clock()
        matches = [r for r in self.reports if r[2].endswith(f"__{tool}") and r[3] == digest]
        # RULE: a report older than five seconds is refused
        self.reports = [r for r in self.reports if now - r[0] <= PROOF_SECONDS]
        # RULE: a report from another session is refused
        mine = [r for r in matches if r in self.reports and self.approver(r[1])]
        if not mine:
            ages = [round(now - r[0], 1) for r in matches]
            logger.warning("%s refused as not proven; ages of matching reports: %s", tool, ages)
            return None
        # RULE: each report is used once
        self.reports.remove(mine[0])
        return mine[0][1]

    def check_in(self) -> None:
        self.seen = self.clock()

    def on_duty(self) -> bool:
        return self.on and self.seen is not None and self.clock() - self.seen < CHECK_IN

    def covers(self, prompt: dict[str, Any]) -> bool:
        """Whether this box waits for the approver, even with the owner at the laptop."""
        # RULE: a box waits for the approver only while it is on duty
        return prompt.get("kind") == "approval" and self.on_duty() and self.role(str(prompt.get("session")))

    def closed(self, prompt: dict[str, Any], how: str) -> None:
        """A box left to the screen when its hook stopped waiting; a timeout is logged."""
        covered = prompt.get("kind") == "approval" and self.role(str(prompt.get("session")))
        if self.on and how == "timeout" and covered and prompt.get("answer") is None:
            self.record(prompt, "timeout", "no answer in time", by="screen")

    def record(self, prompt: dict[str, Any], decision: str, reason: str, by: str = "approver") -> None:
        """One line in the log. It holds a hash of the input, never the input itself."""
        routed = _routed(prompt)
        given = json.dumps(prompt.get("raw_input"), sort_keys=True, ensure_ascii=False)
        # RULE: the approver log keeps each decision without the input text
        held = hashlib.sha256(given.encode()).hexdigest()
        line = {
            "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "session": prompt.get("session"),
            "folder": prompt.get("cwd"),
            "tool": prompt.get("tool"),
            "input_sha256": held,
            "lines": routed.get("lines"),
            "shape": routed.get("shape"),
            "decision": decision,
            "by": by,
            "reason": reason,
        }
        self.log.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.log, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as out:
            out.write(json.dumps(line, ensure_ascii=False) + "\n")


def _routed(prompt: dict[str, Any]) -> dict[str, Any]:
    """How the bridge sorted the box, or nothing while it is still being sorted."""
    routed = prompt.get("routed")
    return routed if isinstance(routed, dict) else {}


def place_of(prompt: dict[str, Any]) -> routing.Place | None:
    """The asking session's folder and its scratchpad, or None when the hook sent no folder."""
    cwd = str(prompt.get("cwd") or "")
    if not os.path.isabs(cwd):
        return None
    project = re.sub(r"[^A-Za-z0-9]", "-", cwd)
    scratch = session_hook._tmp_root() / project / str(prompt.get("session")) / "scratchpad"
    return routing.Place(repo=Path(cwd), scratchpad=scratch, home=Path.home(), owner="")


def judge(prompt: dict[str, Any]) -> str | None:
    """None when the approver may answer this box; otherwise why it is the owner's."""
    place = place_of(prompt)
    if place is None:
        return "no folder to judge it in"
    routed = prompt.get("routed")
    lines = routed.get("lines") if isinstance(routed, dict) else None
    given = prompt.get("raw_input")
    return why_owner(str(prompt.get("tool")), given, lines, place, cwd=place.repo, cut=bool(prompt.get("cut")))


def boxes(book: prompts.Book, duty: Duty, names: dict[str, Any], digest: str) -> list[dict[str, Any]] | dict[str, str]:
    """The approval boxes a hook waits on now, each with whether the approver may answer it.
    Looking is the approver's check-in. `digest` is the hash of this call's arguments."""
    if not duty.on:
        return {"refused": OFF}
    if duty.caller("approver_boxes", digest) is None:
        return {"refused": NOT_PROVEN}
    duty.check_in()
    out = []
    for prompt in book.items.values():
        if prompt.get("kind") != "approval" or prompt.get("answer") is not None or not book.hook_waits(prompt):
            continue
        why = judge(prompt)
        routed = _routed(prompt)
        out.append(
            {
                "id": prompt["id"],
                "session": prompt.get("session"),
                "session_name": names.get(str(prompt.get("session"))),
                "folder": prompt.get("cwd"),
                "tool": prompt.get("tool"),
                "input": prompt.get("input"),
                "lines": routed.get("lines"),
                "shape": routed.get("shape"),
                "asked_at": prompt.get("asked_at"),
                "answerable": why is None,
                "why_owner": why,
            }
        )
    return out


def answer(
    book: prompts.Book,
    duty: Duty,
    prompt_id: str,
    decision: str,
    reason: str,
    settle: Callable[[dict[str, Any], dict[str, Any]], None],
    digest: str,
) -> dict[str, Any]:
    """Answer one box as the approver, once, or say why not. `settle` gives the box its
    answer (answers.settle), and `digest` is the hash of this call's arguments. Nothing here
    awaits, so the hook's own timeout cannot slip in between the check and the answer."""
    if not duty.on:
        return {"refused": OFF}
    caller = duty.caller("approver_answer", digest)
    # RULE: an approver call without its own hooks report is refused
    if caller is None:
        return {"refused": NOT_PROVEN}
    if decision not in ("allow", "deny"):
        return {"refused": "decision is allow or deny"}
    prompt = book.items.get(prompt_id)
    # RULE: an answer after the box fell back to the screen is refused as too late
    if prompt is None or prompt.get("answer") is not None or not book.hook_waits(prompt):
        duty.record(prompt or {"id": prompt_id}, "late", reason)
        return {"refused": TOO_LATE}
    # RULE: the approver never answers its own box
    if prompt.get("session") == caller:
        duty.record(prompt, "refused", OWN_BOX)
        return {"refused": OWN_BOX}
    # RULE: the approver never answers a box the owner holds
    if why := judge(prompt):
        duty.record(prompt, "refused", why)
        return {"refused": f"this box is the owner's: {why}"}
    settle(prompt, {"decision": decision, "reason": reason})
    duty.record(prompt, decision, reason)
    return {"decided": decision, "id": prompt_id, "session": prompt.get("session")}
