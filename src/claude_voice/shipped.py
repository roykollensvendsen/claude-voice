"""Bringing the shipped permission rules to the repositories after an update, without a yes
only where they take rights away (designs: rolling restart v2, §8a and §8b).

``claude-voice permissions`` writes the bridge's shipped rules into each repository's local
settings, but nothing ran it after an update, so new rules lagged for days. After an update the
bridge now compares, per repository, what that command would write with the file on disk:

- a change that only takes rights away is written, and recorded as accepted from the shipped
  table (``shipped@<commit>``), so the sessions can be rolled onto it;
- anything else waits for the owner's typed accept: a widening, a broad narrowing that could
  lock a session out of its own work, a file the owner never accepted or has edited since, and
  the manager's own repository, so a bad rule can never cut off the session that relays the
  owner's answers.

Only folders the org chart places in the bridge's own programme are written. A folder in
another programme, in none, or a chart that cannot be read, is left exactly as it is.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from . import accepted, permissions, safe_git

if TYPE_CHECKING:
    from . import org

WRITTEN, SAME, WAITS, SKIPPED = "written", "same", "waits", "skipped"


@dataclass(frozen=True)
class Outcome:
    folder: Path
    action: str
    why: str = ""


def broad(rule: str) -> bool:
    """A rule that could lock a session out of its own work: a whole tool, such as `Edit` or
    `mcp__server`, or a pattern that is or starts with `*`."""
    if "(" in rule and rule.endswith(")"):
        return rule[rule.index("(") + 1 : -1].startswith("*")
    # RULE: a whole tool or a whole server is a broad rule but one server tool is not
    return not rule.startswith("mcp__") or rule.count("__") < 2


def classify(old: bytes, new: bytes) -> tuple[list[str], list[str]]:
    """The lines of the change that widen a session's rights, and the narrowing ones that are
    broad. A change with neither only takes rights away."""
    lines = accepted.changes(old, new)
    widens = [line.text for line in lines if line.kind == accepted.WIDENS]
    added = [line.text for line in lines if line.kind == accepted.NARROWS and line.text.startswith("+ ")]
    return widens, [text for text in added if broad(text.split(" ", 2)[2])]


def _shipped_bytes(folder: Path, rules: permissions.Rules) -> tuple[bytes, bool]:
    """What `claude-voice permissions` would write to `folder`, and whether that differs."""
    file = folder / permissions.SETTINGS
    before = permissions._read(file)
    after = permissions.merged(before, permissions.for_folder(rules, folder))
    return (json.dumps(after, indent=2, ensure_ascii=False) + "\n").encode("utf-8"), after != before


def _outside(chart: org.Chart, own: org.Programme | None, folder: Path) -> str:
    """Why `folder` is not the bridge's to write, or nothing when it is."""
    if own is None:
        return "the bridge's own programme is not in the org chart, or the chart cannot be read"
    placed = chart.place(str(folder), ()).programme
    if placed is None:
        return "the org chart puts it in no programme"
    # RULE: only folders the chart places in the bridges own programme are written
    if placed is not own:
        return f"the org chart puts it in {placed.code}, whose settings are never written by the bridge"
    return ""


def _waits(file: Path, old: bytes, new: bytes) -> str:
    """Why this change waits for the owner's accept, or nothing when it may be written."""
    entry = accepted.read_record().get(str(file.resolve()))
    # RULE: a file the owner never accepted waits
    if not isinstance(entry, dict) or not file.exists():
        return "its settings file is not accepted yet"
    # RULE: a hand edited file is reported and never overwritten
    if accepted.sha256(old) != entry.get("sha256"):
        return "its settings file changed since it was accepted"
    widens, wide = classify(old, new)
    # RULE: a shipped rule that widens waits for the owners accept
    if widens or wide:
        parts = [f"{len(widens)} widens"] if widens else []
        parts += [f"{len(wide)} BROAD"] if wide else []
        return f"the new rules have {' and '.join(parts)}"
    return ""


def one(
    folder: Path,
    chart: org.Chart,
    own: org.Programme | None,
    manager: Path,
    rules: permissions.Rules,
    sha: str,
    backup_root: Path,
) -> Outcome:
    folder = folder.expanduser().resolve()
    outside = _outside(chart, own, folder)
    if outside:
        return Outcome(folder, SKIPPED, outside)
    file = folder / permissions.SETTINGS
    new, differs = _shipped_bytes(folder, rules)
    if not differs:
        return Outcome(folder, SAME)
    accept = f"run `claude-voice permissions --accept {folder}` and read the WIDENS lines first"
    # RULE: the managers own repository is never written by itself
    if folder == manager:
        return Outcome(folder, WAITS, f"the manager's own repository waits for the owner; {accept}")
    old = file.read_bytes() if file.exists() else b""
    why = _waits(file, old, new)
    if why:
        return Outcome(folder, WAITS, f"{why}; {accept}")
    # RULE: a file changed while being written is never recorded as shipped
    if not _replace_if_unchanged(file, folder, old, new, backup_root):
        return Outcome(folder, WAITS, f"its settings file changed while the new rules were written; {accept}")
    accepted.accept([accepted.Shown(file.resolve(), new, accepted.sha256(new), ())], source=f"shipped@{sha}")
    return Outcome(folder, WRITTEN, f"only takes rights away; recorded as shipped@{sha[:7]}")


def _replace_if_unchanged(file: Path, folder: Path, old: bytes, new: bytes, backup_root: Path) -> bool:
    """Write exactly `new` over `file`, only while it still holds exactly `old`, and say
    whether the file now holds `new`. Nothing is merged with what is on disk."""
    if (file.read_bytes() if file.exists() else b"") != old:
        return False
    if file.exists():
        permissions._backup(file, folder, backup_root, time.time())
    handle, name = tempfile.mkstemp(dir=file.parent, prefix=file.name + ".", suffix=".shipped")
    spare = Path(name)
    try:
        with os.fdopen(handle, "wb") as out:
            out.write(new)
        if file.exists():
            shutil.copymode(file, spare)
        os.replace(spare, file)
    finally:
        spare.unlink(missing_ok=True)
    return file.read_bytes() == new


def sync(
    folders: list[Path],
    chart: org.Chart,
    own_clone: Path | None,
    rules: permissions.Rules,
    sha: str,
    *,
    backup_root: Path,
) -> list[Outcome]:
    """Bring each folder's shipped rules up to date where that only takes rights away."""
    manager = own_clone.expanduser().resolve() if own_clone else Path("/nonexistent")
    own = next((p for p in chart.programmes if p.repo == manager), None)
    return [one(folder, chart, own, manager, rules, sha, backup_root) for folder in folders]


def installed_commit(own_clone: Path | None) -> str:
    """The commit the bridge runs from, so a record names the rules really installed."""
    if own_clone is None:
        return "unknown"
    try:
        out = safe_git.run(
            "-C", str(own_clone), "rev-parse", "HEAD", capture_output=True, text=True, timeout=10, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return out.stdout.strip() if out.returncode == 0 and out.stdout.strip() else "unknown"


def report(done: list[Outcome]) -> list[str]:
    """A line per folder: where it is, what happened, and why."""
    return [f"{o.folder}: {o.action}" + (f" ({o.why})" if o.why else "") for o in done]
