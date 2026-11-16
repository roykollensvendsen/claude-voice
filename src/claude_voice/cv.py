"""cv: the sessions file read from a terminal, the same locally and over SSH (docs/recovery.md).

`cv ls` lists every role and what it is doing; `cv peek <role>` shows the last lines of its
tmux pane. Neither changes anything: a pane is only read, never typed into.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from . import events, roster

WIDTH = 39  # a line of `cv ls` stays under 40 characters, to fit a phone
ORGANISATION_WIDTH = 10
PEEK_LINES = 20
STATES = {"busy": "busy", "shell": "busy", "idle": "idle", "waiting": "needs you"}
GONE = "gone"
NO_FILE = (
    "cv: there is no sessions file at {path} yet. The bridge writes it while it runs; "
    "see whether it does with: systemctl --user status claude-voice\n"
)

Run = Callable[[list[str]], str]


class Refused(Exception):
    """cv cannot answer; the message says why and what to do next."""


def _tmux(command: list[str]) -> str:
    return subprocess.run(command, check=True, capture_output=True, text=True, timeout=5).stdout


def _clip(text: str, width: int) -> str:
    # RULE: a cv ls line fits a phone in 40 characters
    return text if len(text) <= width else text[: width - 1] + "…"


def state(row: roster.Row, live: Mapping[str, dict[str, Any]]) -> str:
    """What the role is doing, by its conversation's running session; gone when none runs it."""
    found = live.get(row.conversation)
    return GONE if found is None else STATES.get(str(found.get("status")), "busy")


def lines(rows: list[roster.Row], live: Mapping[str, dict[str, Any]]) -> list[str]:
    """One line per role: its organisation, its role and its state, in columns."""
    if not rows:
        return []
    rows = sorted(rows, key=lambda r: (r.organisation, r.role))
    states = [state(r, live) for r in rows]
    org_width = min(max(len(r.organisation) for r in rows), ORGANISATION_WIDTH)
    role_width = min(max(len(r.role) for r in rows), WIDTH - org_width - max(map(len, states)) - 2)
    return [
        f"{_clip(r.organisation, org_width):<{org_width}} {_clip(r.role, role_width):<{role_width}} {s}"
        for r, s in zip(rows, states, strict=True)
    ]


def pick(rows: list[roster.Row], wanted: str) -> roster.Row:
    """The row `wanted` names: a role, or `<organisation>/<role>`. Raises Refused."""
    organisation, _, role = wanted.rpartition("/")
    found = [r for r in rows if r.role == role and organisation in ("", r.organisation)]
    if not found:
        raise Refused(f"no role {wanted!r} in the sessions file. `cv ls` lists them")
    if len(found) > 1:
        names = ", ".join(f"{r.organisation}/{r.role}" for r in found)
        raise Refused(f"{wanted!r} is a role in more than one organisation: {names}. Name one of them")
    return found[0]


def pane(target: str) -> str:
    """The pane id in a tmux place as Claude Code writes it (`session:@window.%pane`)."""
    tail = target.rpartition(".")[2]
    return tail if tail.startswith("%") else target


def peek(row: roster.Row, live: Mapping[str, dict[str, Any]], run: Run) -> list[str]:
    """The last lines of the role's tmux pane, read without attaching. Raises Refused."""
    found = live.get(row.conversation)
    if found is None:
        raise Refused(
            f"{row.role} is not running, so there is nothing to show. "
            f"To bring it back: cd {row.folder} && claude --resume {row.conversation}"
        )
    target = str(found.get("tmux") or "")
    if not target:
        raise Refused(f"{row.role} is running, but not in tmux, so cv cannot show it. Look at its own terminal")
    try:
        screen = run(["tmux", "capture-pane", "-p", "-J", "-t", pane(target)])
    except (OSError, subprocess.SubprocessError) as exc:
        said = str(getattr(exc, "stderr", "") or exc).strip()
        raise Refused(f"tmux could not show {row.role} ({said}). See whether it still runs with `cv ls`") from exc
    return screen.rstrip("\n").splitlines()[-PEEK_LINES:]


def _home(env: Mapping[str, str]) -> Path:
    return Path(env.get("HOME") or Path.home())


def main(argv: list[str] | None = None, env: Mapping[str, str] | None = None, run: Run = _tmux) -> int:
    """Parse arguments and answer. Returns a process exit code."""
    home = _home(os.environ if env is None else env)
    parser = argparse.ArgumentParser(prog="cv", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("ls", help="list every role and what it is doing")
    peek_cmd = sub.add_parser("peek", help="show the last lines of a role's tmux pane")
    peek_cmd.add_argument("role", help="a role, or <organisation>/<role>")
    parsed = parser.parse_args(sys.argv[1:] if argv is None else argv)

    path = home / roster.FILE.relative_to("~")
    try:
        rows = roster.read(path)
        if not path.exists():
            sys.stderr.write(NO_FILE.format(path=path))
            return 1
        live = {str(d.get("sessionId")): d for d in events.read_live(home / ".claude" / "sessions")}
        out = lines(rows, live) if parsed.command == "ls" else peek(pick(rows, parsed.role), live, run)
    except (roster.Unreadable, Refused) as exc:
        sys.stderr.write(f"cv: {exc}.\n")
        return 1
    for line in out:
        sys.stdout.write(line + "\n")
    return 0


def entry() -> None:
    sys.exit(main())


if __name__ == "__main__":
    entry()
