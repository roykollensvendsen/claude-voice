"""Why the bridge last restarted, kept so its health answer can say so.

A starting process cannot see why its predecessor stopped. So whoever restarts it on
purpose (the updater, or `claude-voice restart`) writes a small reason file first, and
the next start reads it once and deletes it. Without a fresh file, systemd's count of
automatic restarts and the machine's uptime tell a crash and a boot apart from the rest.

Every function takes the state folder, and everything it asks of the computer, as
arguments, so tests never touch systemd, git or the owner's own files.
"""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import time
import unicodedata
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from logging import getLogger
from pathlib import Path
from typing import Any

UNIT = "claude-voice"
REASON_FILE = "restart-reason.json"
COUNT_FILE = "restarts"  # systemd's NRestarts as last seen
COMMIT_FILE = "last-start-commit"  # the commit the bridge last started on
FRESH_FOR = 10 * 60  # seconds a reason file counts; an older one was left by a run that died
BOOTED_WITHIN = 5 * 60  # seconds of machine uptime that make a start a boot
NOTE_CHARS = 120  # the longest note health quotes; any local program can write one, and the voice reads it

log = getLogger(__name__)


def one_line(text: str) -> str:
    """A note as health may quote it: one line, no control characters, at most NOTE_CHARS."""
    kept = "".join(" " if c.isspace() else c for c in text if c.isspace() or unicodedata.category(c)[0] != "C")
    flat = " ".join(kept.split())
    # RULE: a note is one short line
    return flat if len(flat) <= NOTE_CHARS else flat[: NOTE_CHARS - 1].rstrip() + "…"


def note(
    state_dir: Path, reason: str, by: str, *, text: str | None = None, frm: str | None = None, to: str | None = None
) -> Path:
    """Write why the bridge is about to restart, for its next start to read."""
    body: dict[str, Any] = {"reason": reason, "by": by, "at": datetime.now(UTC).isoformat(timespec="seconds")}
    body.update({k: v for k, v in (("note", one_line(text or "")), ("from", frm), ("to", to)) if v})
    path = state_dir.expanduser() / REASON_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(body), encoding="utf-8")
    tmp.replace(path)
    return path


def take(state_dir: Path) -> dict[str, Any] | None:
    """The reason file's contents, once: it is deleted as it is read."""
    path = state_dir.expanduser() / REASON_FILE
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        body = None
    # RULE: the reason file is read once
    path.unlink(missing_ok=True)
    return body if isinstance(body, dict) else None


def fresh(planned: dict[str, Any] | None, now: float) -> bool:
    """Whether a reason file was written recently enough to be about this start."""
    try:
        written = datetime.fromisoformat(str((planned or {})["at"])).timestamp()
    except (KeyError, ValueError):
        return False
    # RULE: a reason file older than ten minutes is ignored
    return 0 <= now - written < FRESH_FOR


def decide(planned: dict[str, Any] | None, now: float, restarted: bool, uptime: float | None) -> str:
    """Why this start happened, by the rules in the order they are tried."""
    # RULE: a fresh reason file gives its reason
    if fresh(planned, now):
        return one_line(str((planned or {}).get("reason") or "planned"))
    # RULE: a restart by systemd with no reason file is a crash
    if restarted:
        return "crash"
    # RULE: a start soon after the machine booted is a boot
    if uptime is not None and uptime < BOOTED_WITHIN:
        return "boot"
    # RULE: any other start is unplanned
    return "unplanned"


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def _write(path: Path, text: str) -> None:
    with contextlib.suppress(OSError):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def changes(git: Callable[..., str | None], since: str | None, until: str | None) -> int | None:
    """How many commits lie between two starts; None when git cannot say."""
    if not since or not until:
        return None
    # RULE: changes counts the commits since the last start
    out = git("rev-list", "--count", f"{since}..{until}")
    try:
        return int(out) if out is not None else None
    except ValueError:
        return None


def restart_info(
    state_dir: Path,
    *,
    environ: Mapping[str, str] = os.environ,
    nrestarts: Callable[[], int | None] | None = None,
    uptime: Callable[[], float | None] | None = None,
    git: Callable[..., str | None] | None = None,
    now: Callable[[], float] = time.time,
) -> dict[str, Any]:
    """The record of this start: when, why, who asked, and how many commits it brought."""
    at = datetime.fromtimestamp(now(), UTC).isoformat(timespec="seconds")
    record: dict[str, Any] = {"at": at, "reason": None, "by": None, "changes": None, "from": None, "to": None}
    if not environ.get("INVOCATION_ID"):  # not started by systemd, so not the service's own start
        return {**record, "total": None}
    state = state_dir.expanduser()
    git = git or _git
    total = (nrestarts or _nrestarts)()
    restarted = _counted(state, total)
    last, head = _started_on(state, git)
    planned = take(state)
    reason = decide(planned, now(), restarted, (uptime or _uptime)())
    told: dict[str, Any] = planned if planned and fresh(planned, now()) else {}
    if planned and not told:
        log.info(
            "ignored a restart note from %s (%s): written over ten minutes before this start",
            planned.get("at"),
            one_line(str(planned.get("reason"))),
        )
    frm, to = (told["from"], told["to"]) if told.get("from") and told.get("to") else (last, head)
    by = one_line(str(told["by"])) if told.get("by") else None
    record.update(reason=reason, by=by, changes=changes(git, frm, to), total=total)
    record.update({"from": _short(frm), "to": _short(to)})
    if told.get("note"):
        record["note"] = one_line(str(told["note"]))
    return record


def _counted(state: Path, total: int | None) -> bool:
    """Whether systemd's count of its own restarts went up since the last start, kept for the next."""
    seen = _read(state / COUNT_FILE)
    if total is None:
        return False
    _write(state / COUNT_FILE, str(total))
    return seen is not None and seen.isdigit() and total > int(seen)


def _started_on(state: Path, git: Callable[..., str | None]) -> tuple[str | None, str | None]:
    """The commit the last start ran on, and this one's, which is kept for the next."""
    last = _read(state / COMMIT_FILE)
    head = git("rev-parse", "--short", "HEAD")
    if head:
        _write(state / COMMIT_FILE, head)
    return last, head


def _short(commit: str | None) -> str | None:
    return commit[:7] if commit else None


# -- the real computer ---------------------------------------------------------------


def _nrestarts() -> int | None:
    try:
        out = subprocess.run(
            ["systemctl", "--user", "show", UNIT, "-p", "NRestarts", "--value"],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
        return int(out.stdout.strip() or 0)
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def _uptime() -> float | None:
    try:
        return float(Path("/proc/uptime").read_text(encoding="utf-8").split()[0])
    except (OSError, ValueError, IndexError):
        return None


def _git(*args: str) -> str | None:
    here = Path(__file__).resolve().parent  # the bridge's own checkout
    try:
        out = subprocess.run(["git", *args], cwd=here, capture_output=True, text=True, timeout=5, check=True)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() or None


RESTART = ["systemctl", "--user", "restart", UNIT]


def systemctl(command: list[str]) -> int:
    """Run one systemctl command and give back its exit code."""
    try:
        return subprocess.run(command, capture_output=True, timeout=120, check=False).returncode
    except (OSError, subprocess.SubprocessError):
        return 1
