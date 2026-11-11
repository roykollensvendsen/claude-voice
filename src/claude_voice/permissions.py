"""The permission rules a Claude Code session gets in the voice repositories.

The rules live in ``permissions.json`` beside this file, in named groups, and
``docs/permissions.md`` says what each group is for. ``claude-voice permissions``
adds them to each repository's ``.claude/settings.local.json``, which stays out of
git. It adds: the owner's own rules stay as they are, a rule already there is
not added twice, and the file is backed up once, before the first change. The one
thing it takes away is a retired allow rule, which the table names exactly.
"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import Config

TABLE = Path(__file__).with_name("permissions.json")
SETTINGS = Path(".claude/settings.local.json")
BACKUP = ".before-quality-rules"  # the name the first hand-run installer used, so its backups count


class PermissionsError(ValueError):
    pass


@dataclass(frozen=True)
class Rules:
    allow: list[str]
    deny: list[str]
    #: Allow rules an older installer wrote that are now unsafe, removed by their exact text.
    retired: list[str] = field(default_factory=list)


def _unique(rules: list[str]) -> list[str]:
    return list(dict.fromkeys(rules))


def load(table: Path = TABLE) -> Rules:
    """The rules, each group flattened, with every secret file refused to every reader."""
    raw = json.loads(table.read_text(encoding="utf-8"))
    allow = [rule for group in raw["allow"].values() for rule in group]
    deny = [rule for group in raw["deny"].values() for rule in group]
    secrets = raw["secrets"]
    deny += [f"Bash({reader} {path})" for path in secrets["files"] for reader in secrets["readers"]]
    retired = list(raw.get("retired", {}).get("allow", []))
    return Rules(_unique(allow), _unique(deny), _unique(retired))


def folders(cfg: Config) -> list[Path]:
    """The bridge's clones and each plugin's, from its settings: those that exist, once each."""
    named = [cfg.paths.claude_voice, cfg.paths.claude_voice_ui, *(Path(p.cwd) for p in cfg.plugins)]
    found = {path.resolve(): None for path in named if path is not None and path.is_dir()}
    return list(found)


def merged(settings: dict[str, Any], rules: Rules) -> dict[str, Any]:
    """The settings with the rules added after the owner's own, each rule once, and none retired."""
    have = settings.get("permissions") or {}
    if not isinstance(have, dict):
        raise PermissionsError("its permissions are not a table of rules")
    out = dict(have)
    for key, wanted in (("allow", rules.allow), ("deny", rules.deny)):
        # RULE: rules already in the settings are kept
        kept = list(have.get(key) or [])
        if key == "allow":
            # RULE: a retired allow rule is removed
            kept = [rule for rule in kept if rule not in rules.retired]
        for rule in wanted:
            # RULE: a rule the settings already have is not added again
            if rule not in kept:
                kept.append(rule)
        out[key] = kept
    return {**settings, "permissions": out}


@dataclass(frozen=True)
class Outcome:
    folder: Path
    added_allow: int
    added_deny: int
    removed_allow: int = 0
    dry_run: bool = False
    created: bool = False
    backed_up: bool = False

    def line(self) -> str:
        counts = f"{self.added_allow} allow and {self.added_deny} deny rules"
        if not (self.added_allow or self.added_deny or self.removed_allow):
            return f"{self.folder}: already has every rule"
        retired = ""
        if self.removed_allow:
            verb = "remove" if self.dry_run else "removed"
            plural = "" if self.removed_allow == 1 else "s"
            retired = f", and {verb} {self.removed_allow} retired allow rule{plural}"
        if self.dry_run:
            return f"{self.folder}: would add {counts}{retired}"
        notes = [f"created {SETTINGS}"] if self.created else []
        notes += [f"kept the old file as {SETTINGS.name}{BACKUP}"] if self.backed_up else []
        return f"{self.folder}: added {counts}{retired}" + "".join(f"; {note}" for note in notes)


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


def apply(folder: Path, rules: Rules, dry_run: bool = False) -> Outcome:
    """Add the rules to one repository's local settings, or say what that would change."""
    if not folder.is_dir():
        raise PermissionsError(f"{folder} is not a folder, so it was left alone")
    file = folder / SETTINGS
    before = _read(file)
    try:
        after = merged(before, rules)
    except PermissionsError as exc:
        raise PermissionsError(f"{file}: {exc}, so it was left alone") from exc
    old, new = before.get("permissions") or {}, after["permissions"]
    allow, deny = (len([rule for rule in new[key] if rule not in (old.get(key) or [])]) for key in ("allow", "deny"))
    removed = len([rule for rule in old.get("allow") or [] if rule not in new["allow"]])
    outcome = Outcome(folder, allow, deny, removed, dry_run=dry_run, created=not file.exists())
    # RULE: a second run writes nothing
    if after == before:
        return outcome
    # RULE: a dry run writes nothing
    if dry_run:
        return outcome
    backup = file.with_name(file.name + BACKUP)
    backed_up = False
    # RULE: the backup is written only once
    if file.exists() and not backup.exists():
        shutil.copy2(file, backup)
        backed_up = True
    _write(file, after)
    return Outcome(folder, allow, deny, removed, created=outcome.created, backed_up=backed_up)
