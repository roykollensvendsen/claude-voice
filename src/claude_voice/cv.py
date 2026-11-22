"""cv: the sessions file read from a terminal, the same locally and over SSH (docs/recovery.md).

`cv ls` lists every role and what it is doing; `cv peek <role>` shows the last lines of its
tmux pane. Neither changes anything: a pane is only read, never typed into. `cv up <role>`
brings a role whose session has ended back in a tmux window of its own, started from the
owner's role definition (worker-on-demand-v1).
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import config, events, org, procs, relaunch, roles, roster

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


Refused = relaunch.Refused  # cv cannot answer; the message says why and what to do next


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


START_SECONDS = 30.0  # how long a start holds its lock while the new session appears


@dataclass
class Start:
    """What `cv up` needs from the owner's files and the machine."""

    table: dict[str, dict[str, Any]]  # the roles table, as the bridge reads it
    chart: org.Chart
    claude: str | None
    tmux: str | None
    locks: Path
    live: Callable[[], set[str]]  # the conversations running now
    wait: Callable[[str], None]  # returns once the conversation runs, or after START_SECONDS


def _session(chart: org.Chart, folder: Path, role: str) -> str:
    """The tmux session a role starts in: its programme's code. Raises Refused unless the
    role's name and its folder place it in exactly one programme."""
    by_name, by_folder = chart.place(None, [role]).programme, chart.place(str(folder), []).programme
    # RULE: a role started by cv up sits in exactly one programme
    if by_name and by_folder and by_name.code != by_folder.code:
        raise Refused(f"{role} is placed in both {by_folder.code} and {by_name.code}; fix the org files")
    found = by_name or by_folder
    if found is None:
        raise Refused(f"{role} is in no programme, so it has no tmux session to start in; add it to an org file")
    return relaunch.name(found.code)


def _definition(how: Start, row: roster.Row) -> tuple[Path, str | None]:
    """The role's folder and settings, from the owner's roles table alone. Raises Refused."""
    spec = how.table.get(row.role)
    if spec is None:
        raise Refused(f"there is no role definition for {row.role}; start it fresh with start_role")
    try:
        return roles.folder_for(spec), roles.settings_for(spec, row.role)
    except LookupError as exc:
        raise Refused(str(exc).rstrip(".")) from None


def up(row: roster.Row, how: Start, run: Run) -> list[str]:
    """Bring the role's conversation back in a new tmux window, built from its definition.
    Returns what to say. Raises Refused."""
    conversation = relaunch.conversation(row.role, row.conversation)
    window = relaunch.name(row.role)
    with relaunch.starting(how.locks, window):
        # RULE: a conversation that is running is not started again
        if conversation in how.live():
            raise Refused(f"{row.role} is running; see it with `cv peek {row.role}`")
        folder, settings = _definition(how, row)
        session = _session(how.chart, folder, row.role)
        if not how.claude or not how.tmux:
            raise Refused("cv up needs tmux and claude on this machine")
        argv = relaunch.build(os.path.realpath(how.claude), conversation, settings, name=window)
        said = _differed(row, argv, folder)
        _open(run, relaunch.window(how.tmux, session, window, str(folder), argv))
        how.wait(conversation)
    return [*said, f"{row.role}: started in tmux {session}:{window}; follow it with `cv peek {row.role}`"]


def _differed(row: roster.Row, argv: list[str], folder: Path) -> list[str]:
    """The line saying how the last start differed from the definition; none without a record."""
    if not row.argv:
        return []
    moved = ["changed folder"] if row.folder and Path(row.folder) != folder else []
    line = relaunch.difference_line(row.role, [*relaunch.differences(row.argv, argv), *moved])
    return [line] if line else []


def _open(run: Run, command: list[str]) -> None:
    """Run tmux's command; what tmux says when it cannot is passed on. Raises Refused."""
    try:
        run(command)
    except (OSError, subprocess.SubprocessError) as exc:
        said = str(getattr(exc, "stderr", "") or exc).strip()
        raise Refused(f"tmux could not open the window ({said})") from exc


def _running(sessions: Path) -> set[str]:
    return {str(d.get("sessionId")) for d in events.read_live(sessions)}


def _start(home: Path, env: Mapping[str, str]) -> Start:
    """The roles table, the org chart and the programs, read as the bridge reads them."""
    try:
        cfg = config.load(config.FILE, own_clone=roles.own_clone(), env=env)
    except config.ConfigError as exc:
        raise Refused(str(exc).rstrip(".")) from None
    table = roles.load(roles.USER_FILE, {"folder": str(cfg.paths.contribute or ""), "upstream": cfg.repos.claude_voice})
    root = Path(env.get("CLAUDE_VOICE_ROOT") or "~/src").expanduser()
    chart = org.load(org.known_folders(root, [cfg.paths.claude_voice, *(p.cwd for p in cfg.plugins)]))
    sessions = home / ".claude" / "sessions"

    def wait(conversation: str) -> None:
        deadline = time.monotonic() + START_SECONDS
        while conversation not in _running(sessions) and time.monotonic() < deadline:
            time.sleep(0.5)

    return Start(
        table,
        chart,
        procs.find("claude"),
        procs.find("tmux"),
        home / roster.FILE.parent.relative_to("~") / "locks",
        lambda: _running(sessions),
        wait,
    )


def _answer(
    parsed: argparse.Namespace, rows: list[roster.Row], home: Path, env: Mapping[str, str], run: Run
) -> list[str]:
    """What the command asked for says. Raises Refused."""
    if parsed.command == "up":
        return up(pick(rows, parsed.role), _start(home, env), run)
    live = {str(d.get("sessionId")): d for d in events.read_live(home / ".claude" / "sessions")}
    return lines(rows, live) if parsed.command == "ls" else peek(pick(rows, parsed.role), live, run)


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
    up_cmd = sub.add_parser("up", help="bring a role whose session has ended back in tmux")
    up_cmd.add_argument("role", help="a role, or <organisation>/<role>")
    environment = os.environ if env is None else env
    parsed = parser.parse_args(sys.argv[1:] if argv is None else argv)

    path = home / roster.FILE.relative_to("~")
    try:
        rows = roster.read(path)
        if not path.exists():
            sys.stderr.write(NO_FILE.format(path=path))
            return 1
        out = _answer(parsed, rows, home, environment, run)
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
