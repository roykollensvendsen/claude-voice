"""The owner's ask waivers: named ask rules kept out of named folders (ask-waivers v1.1).

``claude-voice permissions`` adds every shipped ask to each folder's local settings, and adds
back one the owner deleted. ``~/.config/claude-voice/ask-waivers.toml`` keeps an ask out of one
folder for good. The owner writes it by hand; no command writes it, and the shipped deny rules
keep every session from writing it. Only the exact ask texts the shipped table lists under
``"waivable asks"`` can be waived. A deny is never waived and an allow never touched.

A file that cannot be trusted waives nothing, so every ask comes back, and the run's first
line says why: that is the safe side.
"""

from __future__ import annotations

import os
import stat
import tomllib
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .permissions import Rules

FILE = Path("~/.config/claude-voice/ask-waivers.toml")
#: The longest `why` shown; a longer one is cut, not refused.
WHY_LENGTH = 200
KEYS = {"path", "asks", "why"}


class Refused(ValueError):
    """Why the file waives nothing."""


def shown(why: str) -> str:
    """The owner's reason as data: one line, no control characters, cut, and in quotes."""
    flat = "".join(" " if ch in "\r\n\t" else ch for ch in why)
    # RULE: a why is shown without control characters
    clean = "".join(ch for ch in flat if not unicodedata.category(ch).startswith("C"))
    # RULE: a why is cut to 200 characters
    return f'"{clean[:WHY_LENGTH]}"'


@dataclass(frozen=True)
class Waiver:
    #: The folder, resolved, as the run's folders are.
    path: Path
    asks: tuple[str, ...]
    why: str

    def line(self, dry_run: bool) -> str:
        """What a run says about this waiver."""
        count = len(self.asks)
        verb = "would keep" if dry_run else "kept"
        noun = "ask" if count == 1 else "asks"
        return f"{verb} {count} {noun} out by the owner's waiver: {', '.join(self.asks)} (why: {shown(self.why)})"


@dataclass(frozen=True)
class Waivers:
    entries: tuple[Waiver, ...] = ()
    #: Why the file waives nothing, or empty when it was read.
    problem: str = ""
    notes: tuple[str, ...] = ()

    def lines(self) -> list[str]:
        """What a run says first: why the file waives nothing, then each note."""
        return [self.problem] * bool(self.problem) + list(self.notes)

    def for_folder(self, folder: Path) -> Waiver | None:
        """The waiver for one folder, matched by its resolved path; None when it has none."""
        real = folder.resolve()
        return next((w for w in self.entries if w.path == real), None)


def read(rules: Rules, file: Path | None = None) -> Waivers:
    """The owner's waivers, judged against the shipped table; none when there is no file."""
    file = file if file is not None else FILE.expanduser()
    try:
        data = _plain_bytes(file)
        if data is None:
            # RULE: no waiver file means no waivers
            return Waivers()
        return _parsed(data, rules)
    except Refused as exc:
        # RULE: a waiver file that cannot be trusted waives nothing
        return Waivers(problem=f"{file}: {exc}; it waives nothing, so every ask stays")


def _plain_bytes(file: Path) -> bytes | None:
    """The file's bytes, or None when it is missing. Raises Refused for a link or anything
    that is not a plain file, without waiting on a pipe."""
    # RULE: a linked waiver file is refused
    no_link = getattr(os, "O_NOFOLLOW", 0)
    # RULE: a pipe in place of the waiver file is refused without waiting
    no_wait = getattr(os, "O_NONBLOCK", 0)
    try:
        fd = os.open(file, os.O_RDONLY | no_link | no_wait)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise Refused(f"it could not be read ({exc.strerror})") from None
    try:
        # RULE: only a plain file is read as the waiver file
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise Refused("it is not a plain file")
        data = b""
        while chunk := os.read(fd, 1 << 16):
            data += chunk
        return data
    except OSError as exc:
        raise Refused(f"it could not be read ({exc.strerror})") from None
    finally:
        os.close(fd)


def _parsed(data: bytes, rules: Rules) -> Waivers:
    try:
        raw = tomllib.loads(data.decode("utf-8"))
    except UnicodeDecodeError:
        raise Refused("it is not UTF-8 text") from None
    except tomllib.TOMLDecodeError as exc:
        raise Refused(f"it is not valid TOML ({exc})") from None
    folders = raw.get("folder", [])
    if set(raw) - {"folder"} or not isinstance(folders, list):
        raise Refused("it may hold only [[folder]] tables")
    entries: list[Waiver] = []
    notes: list[str] = []
    for table in folders:
        waiver = _entry(table, rules, notes)
        # RULE: a folder named twice refuses the waiver file
        if any(w.path == waiver.path for w in entries):
            raise Refused(f"it names {shown(str(waiver.path))} twice")
        entries.append(waiver)
    return Waivers(tuple(entries), notes=tuple(notes))


def _entry(table: Any, rules: Rules, notes: list[str]) -> Waiver:
    """One [[folder]] table, checked whole. Raises Refused."""
    if not isinstance(table, dict):
        raise Refused("each [[folder]] must be a table")
    if extra := sorted(set(table) - KEYS):
        named = ", ".join(shown(key) for key in extra)
        raise Refused(f"a [[folder]] holds {named}, and may hold only {', '.join(sorted(KEYS))}")
    path, asks, why = table.get("path"), table.get("asks"), table.get("why")
    # RULE: a relative folder path refuses the waiver file
    if not isinstance(path, str) or not Path(path).is_absolute():
        raise Refused(f"the folder path {shown(str(path))} is not absolute")
    if not isinstance(asks, list) or not all(isinstance(a, str) for a in asks):
        raise Refused(f"asks for {shown(path)} is not a list of rule texts")
    # RULE: a waiver without a why refuses the waiver file
    if not isinstance(why, str) or not why.strip():
        raise Refused(f"the waiver for {shown(path)} has no why")
    real = _resolved(path)
    if not real.is_dir():
        # RULE: a waiver for a folder that is gone is noted once
        notes.append(f"the owner's waiver names {shown(path)}, which is not a folder now; it does nothing")
    kept = tuple(ask for ask in asks if _waivable(ask, rules, path, notes))
    return Waiver(real, kept, why)


def _resolved(path: str) -> Path:
    """The folder's real path. Raises Refused for one the system cannot resolve, such as one
    with a NUL in it, or a name Windows does not allow."""
    try:
        return Path(path).resolve()
    except (OSError, ValueError):
        # RULE: a folder path the system cannot resolve refuses the waiver file
        raise Refused(f"the folder path {shown(path)} cannot be resolved") from None


def _waivable(ask: str, rules: Rules, path: str, notes: list[str]) -> bool:
    """Whether one text may be waived; False for a retired ask, which is noted. Raises Refused."""
    said = shown(ask)  # the file's own text, shown as data
    # RULE: a deny is never waived
    if ask in rules.deny:
        raise Refused(f"{said} is a deny rule, and a deny is never waived")
    # RULE: a waiver never touches an allow rule
    if ask in rules.allow:
        raise Refused(f"{said} is an allow rule; a waiver only keeps asks out")
    # RULE: a retired ask in the waiver file is skipped with a note
    if ask in rules.retired_ask:
        notes.append(f"the owner's waiver for {shown(path)} names {said}, a retired ask; it was skipped")
        return False
    # RULE: only an ask the table marks waivable is waived
    if ask not in rules.waivable:
        raise Refused(f"{said} is not an ask the shipped table lets a folder waive")
    return True
