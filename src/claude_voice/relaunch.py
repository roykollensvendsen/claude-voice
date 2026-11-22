"""Bringing a role's conversation back in tmux (worker-on-demand-v1, v1.1 b1, b2 and f1).

The start command is built from the owner's role definition, never replayed from what a
session last ran with. What it last ran with is recorded only to say how it differed.
"""

from __future__ import annotations

import fcntl
import os
import re
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path

MODEL = "claude-opus-5-5"
CONVERSATION = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
NAME = re.compile(r"[a-z0-9-]{1,40}")
RESUME = "--resume"


class Refused(Exception):
    """Not done; the message says why and what to do instead."""


def recorded_argv(pid: int, claude: str, proc: Path = Path("/proc")) -> tuple[str, ...] | None:
    """The command line process `pid` runs, when it is claude itself; otherwise None.
    Only its command line is read, never its environment."""
    folder = proc / str(pid)
    try:
        # RULE: only claude's own command line is recorded
        if os.path.realpath(folder / "exe") != os.path.realpath(claude):
            return None
        raw = (folder / "cmdline").read_bytes()
    except OSError:
        return None
    return tuple(part.decode("utf-8", "replace") for part in raw.split(b"\0")[:-1])


def conversation(role: str, value: str) -> str:
    """The row's conversation id, when it is a lowercase UUID. Raises Refused."""
    # RULE: a conversation id must be a lowercase uuid
    if not CONVERSATION.fullmatch(value):
        raise Refused(f"row for {role} has no valid conversation id; start it fresh with start_role")
    return value


def name(value: str) -> str:
    """A tmux session or window name, when it is safe as one. Raises Refused."""
    # RULE: a tmux name is short lowercase letters digits and dashes
    if not NAME.fullmatch(value):
        raise Refused(f"{value!r} cannot name a tmux session or window; use lowercase letters, digits and dashes")
    return value


def build(claude: str, conversation: str, settings: str | None, name: str | None = None) -> list[str]:
    """The whole start command, from the definition alone: claude, the conversation, the role's
    settings and the model, plus a session name when the role entry gives one."""
    argv = [claude, RESUME, conversation]
    if settings is not None:
        argv += ["--settings", settings]
    argv += ["--model", MODEL]
    if name is not None:
        argv += ["--name", name]
    return argv


def _flags(argv: Sequence[str]) -> dict[str, str | None]:
    """Each flag in a command line with its value, if it has one; the program name is skipped."""
    found: dict[str, str | None] = {}
    rest = list(argv[1:])
    while rest:
        word = rest.pop(0)
        flag, equals, value = word.partition("=")
        if not word.startswith("-"):
            flag, value = "<argument>", word
        elif not equals:
            value = rest.pop(0) if rest and not rest[0].startswith("-") else ""
        found[flag] = value
    return found


def differences(recorded: Sequence[str], built: Sequence[str]) -> list[str]:
    """How the recorded command differs from the built one, by flag name only, never by value.
    The conversation id after --resume is not a difference."""
    then, now = _flags(recorded), _flags(built)
    found = [f"extra {f}" for f in then if f not in now]
    found += [f"missing {f}" for f in now if f not in then]
    # RULE: a difference names the flag and never its value
    found += [f"changed {f}" for f in now if f in then and f != RESUME and then[f] != now[f]]
    return found


def difference_line(role: str, found: Sequence[str]) -> str | None:
    """The one line said when the last start differed from the definition, or None."""
    if not found:
        return None
    return f"{role}: the last start differed from the role definition: {', '.join(found)}; started from the definition."


def window(tmux: str, session: str, window: str, folder: str, argv: Sequence[str]) -> list[str]:
    """The tmux command that opens the role's window, as an argument list: no shell reads it,
    and no keys are typed into any pane."""
    return [tmux, "new-window", "-d", "-t", f"{session}:", "-n", window, "-c", folder, "--", *argv]


@contextmanager
def starting(folder: Path, role: str) -> Iterator[None]:
    """Hold the role's start lock while it is checked and started. Raises Refused when
    another start of the same role holds it."""
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = folder / f"start-{name(role)}.lock"
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            # RULE: one start of a role at a time
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Refused(f"{role} is being started; wait for it, then look with `cv ls`") from None
        yield
    finally:
        os.close(fd)
