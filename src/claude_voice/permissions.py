"""The permission rules a Claude Code session gets in the voice repositories.

The rules live in ``permissions.json`` beside this file, in named groups, and
``docs/permissions.md`` says what each group is for. ``claude-voice permissions``
adds them to each repository's ``.claude/settings.local.json``, which stays out of
git. It adds allow, ask and deny rules: the owner's own rules stay as they are, a
rule already there is not added twice, and the old file is copied under the state
folder before each change, never into the repository. The one thing it takes away is
a retired allow or ask rule, which
the table names exactly.
"""

from __future__ import annotations

import json
import os
import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .config import STATE_DIR, Config

TABLE = Path(__file__).with_name("permissions.json")
SETTINGS = Path(".claude/settings.local.json")
#: Where the old files go: outside every repository, so a backup never makes a checkout dirty
#: (plugins approve refuses a plugin whose checkout has untracked files).
BACKUPS = STATE_DIR / "settings-backups"


def backup_root(home: Path) -> Path:
    """The backup folder under a given home folder."""
    return home / BACKUPS.relative_to("~")


class PermissionsError(ValueError):
    pass


@dataclass(frozen=True)
class Rules:
    allow: list[str]
    deny: list[str]
    #: Retired allow rules: an older table wrote them and they are gone now, removed by exact text.
    retired: list[str] = field(default_factory=list)
    #: What a session asks the owner before, even where an allow rule would let it through.
    ask: list[str] = field(default_factory=list)
    #: Ask rules an older table wrote that are gone now, removed by their exact text.
    retired_ask: list[str] = field(default_factory=list)

    def retired_from(self, key: str) -> list[str]:
        return {"allow": self.retired, "ask": self.retired_ask}.get(key, [])


def _unique(rules: list[str]) -> list[str]:
    return list(dict.fromkeys(rules))


def _flat(groups: dict[str, list[str]]) -> list[str]:
    return [rule for group in groups.values() for rule in group]


def _for_each_branch(branches: list[str], forms: list[str]) -> list[str]:
    return [f"Bash({form.replace('{branch}', branch)})" for branch in branches for form in forms]


def load(table: Path = TABLE) -> Rules:
    """The rules, each group flattened; every protected branch refused to every forced push
    and asked for before every other push; every secret file refused to every reader; and
    every device tool asked for under each name the bridge is known by."""
    raw = json.loads(table.read_text(encoding="utf-8"))
    allow, ask, deny = _flat(raw["allow"]), _flat(raw.get("ask", {})), _flat(raw["deny"])
    protected = raw["protected branches"]
    # RULE: a forced push can never reach main or master
    deny += _for_each_branch(protected["branches"], protected["forced pushes"])
    # RULE: any other push to main or master asks first
    ask += _for_each_branch(protected["branches"], protected.get("other pushes", []))
    secrets = raw["secrets"]
    deny += [f"Bash({reader} {path})" for path in secrets["files"] for reader in secrets["readers"]]
    devices = raw.get("device tools", {"servers": [], "tools": []})
    # RULE: a tool that moves something in the house asks first, by every name it is seen under
    ask += [f"mcp__{server}__{tool}" for server in devices["servers"] for tool in devices["tools"]]
    retired = raw.get("retired", {})
    return Rules(
        _unique(allow),
        _unique(deny),
        _unique(retired.get("allow", [])),
        ask=_unique(ask),
        retired_ask=_unique(retired.get("ask", [])),
    )


def folders(cfg: Config) -> list[Path]:
    """The bridge's clones, the phone app's and each plugin's, from its settings: those that exist, once each."""
    paths = cfg.paths
    named = [paths.claude_voice, paths.claude_voice_ui, paths.claude_voice_mobile, *(Path(p.cwd) for p in cfg.plugins)]
    found = {path.resolve(): None for path in named if path is not None and path.is_dir()}
    return list(found)


def merged(settings: dict[str, Any], rules: Rules) -> dict[str, Any]:
    """The settings with the rules added after the owner's own, each rule once, and none retired."""
    have = settings.get("permissions") or {}
    if not isinstance(have, dict):
        raise PermissionsError("its permissions are not a table of rules")
    out = dict(have)
    for key, wanted in (("allow", rules.allow), ("ask", rules.ask), ("deny", rules.deny)):
        if key in have or wanted:
            out[key] = _merged_list(have, key, wanted, rules)
    return {**settings, "permissions": out}


def _merged_list(have: dict[str, Any], key: str, wanted: list[str], rules: Rules) -> list[str]:
    """One kind of rule: the owner's own first, less any retired, then each wanted rule once."""
    # RULE: rules already in the settings are kept
    kept = list(have.get(key) or [])
    # RULE: a retired allow or ask rule is removed
    kept = [rule for rule in kept if rule not in rules.retired_from(key)]
    for rule in wanted:
        # RULE: a rule the settings already have is not added again
        if rule not in kept:
            kept.append(rule)
    return kept


@dataclass(frozen=True)
class Outcome:
    folder: Path
    added_allow: int
    added_deny: int
    removed_allow: int = 0
    dry_run: bool = False
    created: bool = False
    #: Where the old file was copied before this change, if there was one.
    backup: Path | None = None
    added_ask: int = 0
    removed_ask: int = 0

    def line(self) -> str:
        asks = f", {self.added_ask} ask" if self.added_ask else ""
        counts = f"{self.added_allow} allow{asks} and {self.added_deny} deny rules"
        if not any((self.added_allow, self.added_ask, self.added_deny, self.removed_allow, self.removed_ask)):
            return f"{self.folder}: already has every rule"
        retired = self._retired_note()
        if self.dry_run:
            return f"{self.folder}: would add {counts}{retired}"
        notes = [f"created {SETTINGS}"] if self.created else []
        notes += [f"kept the old file as {self.backup}"] if self.backup else []
        return f"{self.folder}: added {counts}{retired}" + "".join(f"; {note}" for note in notes)

    def _retired_note(self) -> str:
        """The words about removed retired rules, or nothing when none were removed."""
        parts = [
            f"{count} retired {kind} rule{'' if count == 1 else 's'}"
            for kind, count in (("allow", self.removed_allow), ("ask", self.removed_ask))
            if count
        ]
        if not parts:
            return ""
        verb = "remove" if self.dry_run else "removed"
        return f", and {verb} {' and '.join(parts)}"


def _read(file: Path) -> dict[str, Any]:
    if not file.exists():
        return {}
    try:
        settings = json.loads(file.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise PermissionsError(f"{file} is not JSON ({exc}), so it was left alone") from exc
    if not isinstance(settings, dict):
        raise PermissionsError(f"{file} is not a JSON object, so it was left alone")
    return settings


def _write(file: Path, settings: dict[str, Any]) -> None:
    file.parent.mkdir(parents=True, exist_ok=True)
    temporary = file.with_name(file.name + ".tmp")
    temporary.write_text(json.dumps(settings, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if file.exists():
        shutil.copymode(file, temporary)
    os.replace(temporary, file)


def _missing(rules: dict[str, Any], others: dict[str, Any], key: str) -> int:
    """How many of one kind of rule the first table has and the second has not."""
    return len([rule for rule in rules.get(key) or [] if rule not in (others.get(key) or [])])


def _changes(folder: Path, before: dict[str, Any], after: dict[str, Any]) -> Outcome:
    """How many rules of each kind the merge added, and how many allow and ask rules it removed."""
    old, new = before.get("permissions") or {}, after["permissions"]
    return Outcome(
        folder,
        added_allow=_missing(new, old, "allow"),
        added_deny=_missing(new, old, "deny"),
        added_ask=_missing(new, old, "ask"),
        removed_allow=_missing(old, new, "allow"),
        removed_ask=_missing(old, new, "ask"),
    )


def _backup(file: Path, folder: Path, root: Path, now: float) -> Path:
    """Copy the file to a new backup named for the repository and the time, beside any older one."""
    stamp = datetime.fromtimestamp(now, UTC).strftime("%Y%m%dT%H%M%SZ")
    target = root / folder.resolve().name
    target.mkdir(parents=True, exist_ok=True)
    data, count = file.read_bytes(), 1
    while True:
        backup = target / f"{file.name}.{stamp}" if count == 1 else target / f"{file.name}.{stamp}-{count}"
        try:
            # RULE: an older backup is never overwritten
            with backup.open("xb") as out:
                out.write(data)
        except FileExistsError:
            count += 1
            continue
        shutil.copystat(file, backup)
        return backup


def apply(
    folder: Path,
    rules: Rules,
    dry_run: bool = False,
    *,
    backup_root: Path,
    clock: Callable[[], float] = time.time,
) -> Outcome:
    """Add the rules to one repository's local settings, or say what that would change.

    Before a change the old file is copied into ``backup_root``, in a folder named for the
    repository, never into the repository itself."""
    if not folder.is_dir():
        raise PermissionsError(f"{folder} is not a folder, so it was left alone")
    file = folder / SETTINGS
    before = _read(file)
    try:
        after = merged(before, rules)
    except PermissionsError as exc:
        raise PermissionsError(f"{file}: {exc}, so it was left alone") from exc
    counts = _changes(folder, before, after)
    outcome = replace(counts, dry_run=dry_run, created=not file.exists())
    # RULE: a second run writes nothing
    if after == before:
        return outcome
    # RULE: a dry run writes nothing
    if dry_run:
        return outcome
    # RULE: the old file is kept outside the repository before every change
    backup = _backup(file, folder, backup_root, clock()) if file.exists() else None
    _write(file, after)
    return replace(counts, created=outcome.created, backup=backup)
