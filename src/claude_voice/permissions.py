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

import contextlib
import functools
import json
import os
import re
import shlex
import shutil
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import owner_rules, waivers
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
    #: The exact ask texts the owner's waiver file may keep out of a folder (ask-waivers v1.1).
    waivable: list[str] = field(default_factory=list)
    #: The asks the owner's waiver keeps out of this one folder: never added, never removed.
    waived: tuple[str, ...] = ()

    def retired_from(self, key: str) -> list[str]:
        return {"allow": self.retired, "ask": self.retired_ask}.get(key, [])


def _unique(rules: list[str]) -> list[str]:
    return list(dict.fromkeys(rules))


def _flat(groups: dict[str, list[str]]) -> list[str]:
    return [rule for group in groups.values() for rule in group]


def _for_each_branch(branches: list[str], forms: list[str]) -> list[str]:
    return [f"Bash({form.replace('{branch}', branch)})" for branch in branches for form in forms]


# Claude Code does the matching, and this is a small faithful copy of it, the one the tests and
# the dry run both use. A rule without `*` matches the whole command exactly. With `*`, each `*`
# matches any run of characters, spaces included, and the rule must match the whole command. A
# rule ending in `:*` is the older prefix form instead: what stands before the colon is a plain
# prefix, with no wildcard in it, so `Bash(git push* :*)` matches only commands that begin with
# "git push* " itself, which none does. Quotes are taken off the command first, the way a shell
# would, so `git push origin ':feature'` is matched as `git push origin :feature`. It judges one
# simple command; Claude Code splits a chain such as `a && b` first, and this copy does not.


def matches(rule: str, command: str) -> bool:
    """Whether a shell rule matches a command the way Claude Code reads it; never another tool's."""
    if not (rule.startswith("Bash(") and rule.endswith(")")):
        return False
    pattern = rule[len("Bash(") : -1]
    command = _spoken(command)
    if pattern.endswith(":*"):
        return command.startswith(pattern[:-2])
    if "*" not in pattern:
        return command == pattern
    return _glob(pattern).fullmatch(command) is not None


@functools.cache
def _spoken(command: str) -> str:
    """The command as Claude Code compares it, its quoting undone; built once per command."""
    with contextlib.suppress(ValueError):  # unbalanced quotes: matched as written
        return " ".join(shlex.split(command))
    return command


@functools.cache
def _glob(pattern: str) -> re.Pattern[str]:
    """A rule's pattern as a regular expression, built once: the rule tables are large."""
    return re.compile(".*".join(re.escape(part) for part in pattern.split("*")), re.DOTALL)


def decision(rules: Rules, command: str) -> str:
    """ "refused", "asked" or "allowed", in Claude Code's order: deny, then ask, then allow."""
    if any(matches(rule, command) for rule in rules.deny):
        return "refused"
    if any(matches(rule, command) for rule in rules.ask):
        return "asked"
    if any(matches(rule, command) for rule in rules.allow):
        return "allowed"
    return "asked"


def _refuse_prefix_rules(rules: list[str]) -> None:
    """PermissionsError for a rule ending in ':*': Claude Code reads that older form as a plain
    prefix with no wildcard in it, so the rule would never apply as written."""
    # RULE: a rule ending in a colon and a star is refused
    if old := next((r for r in rules if r.endswith(":*)")), None):
        raise PermissionsError(f"{old} ends in ':*', which Claude Code reads as a plain prefix, not a wildcard")


def voice_only_machines(table: Path = TABLE) -> list[str]:
    """The machines the shipped table refuses voice-only confirms for, whatever the settings say."""
    raw = json.loads(table.read_text(encoding="utf-8"))
    return list(raw.get("voice only", {}).get("machines", []))


def reapply_note(machine: str, command: str = "claude-voice", table: Path = TABLE) -> str | None:
    """What to run so a newly named machine's voice-only confirms are refused too; None when
    the shipped table already covers it."""
    if machine in voice_only_machines(table):
        return None
    return (
        f"To refuse {machine}'s voice-only confirms, such as {machine}_sms_send_confirm, to every "
        f"session, run: {command} permissions"
    )


def _voice_only(voice: dict[str, Any], machines: Iterable[str]) -> list[str]:
    """Each voice-only tool, for the table's machines and `machines`, under each bridge name."""
    named = _unique([*voice.get("machines", []), *machines])
    # RULE: a confirm only the voice may call is refused to every session by every name
    return [f"mcp__{s}__{m}_{tool}" for s in voice["servers"] for m in named for tool in voice["tools"]]


def _waivable(texts: list[str], ask: list[str]) -> list[str]:
    """The ask texts a waiver may keep out, each once. Raises PermissionsError for one the table
    does not ship as an ask."""
    # RULE: only an ask the table ships can be marked waivable
    if stray := next((text for text in texts if text not in ask), None):
        raise PermissionsError(f"{stray} is marked waivable but is not one of the table's ask rules")
    return _unique(texts)


def load(table: Path = TABLE, machines: Iterable[str] = ()) -> Rules:
    """The rules, each group flattened; every protected branch refused to every forced push
    and to every other push; every secret file refused to every reader; every device tool
    asked for under each name the bridge is known by; and every voice-only confirm refused
    for each of `machines` and the table's own, under each of the bridge's names."""
    raw = json.loads(table.read_text(encoding="utf-8"))
    allow, ask, deny = _flat(raw["allow"]), _flat(raw.get("ask", {})), _flat(raw["deny"])
    protected = raw["protected branches"]
    # RULE: a forced push can never reach main or master
    deny += _for_each_branch(protected["branches"], protected["forced pushes"])
    # RULE: any other push to main or master is refused
    deny += _for_each_branch(protected["branches"], protected.get("other pushes", []))
    secrets = raw["secrets"]
    deny += [f"Bash({reader} {path})" for path in secrets["files"] for reader in secrets["readers"]]
    lost = raw.get("what cannot be got back", {"removers": [], "targets": []})
    # RULE: removing what cannot be got back is refused
    deny += [f"Bash({remover} {target})" for remover in lost["removers"] for target in lost["targets"]]
    # RULE: every writing command into such a path is refused
    deny += owner_rules.runs_as_owner(raw.get("what later runs as the owner", {}))
    devices = raw.get("device tools", {"servers": [], "tools": []})
    # RULE: a tool that moves something in the house asks first, by every name it is seen under
    ask += [f"mcp__{server}__{tool}" for server in devices["servers"] for tool in devices["tools"]]
    deny += _voice_only(raw.get("voice only", {"servers": [], "tools": []}), machines)
    _refuse_prefix_rules(allow + ask + deny)
    waivable = _waivable(raw.get("waivable asks", []), ask)
    retired = raw.get("retired", {})
    return Rules(
        _unique(allow),
        _unique(deny),
        _unique(retired.get("allow", [])),
        ask=_unique(ask),
        retired_ask=_unique(retired.get("ask", [])),
        waivable=waivable,
    )


def for_folder(rules: Rules, folder: Path, home: Path | None = None, uid: int | None = None) -> Rules:
    """The rules as they are written for one repository: {uid} is the owner's user id, {home}
    the home folder, {repo} the repository folder's name and {folder} its whole path."""
    home = home or Path.home()
    uid = uid if uid is not None else getattr(os, "getuid", lambda: 0)()
    values = {"{uid}": str(uid), "{home}": str(home), "{repo}": folder.name, "{folder}": str(folder)}

    def filled(found: list[str]) -> list[str]:
        out = []
        for rule in found:
            for key, value in values.items():
                rule = rule.replace(key, value)
            out.append(rule)
        return _unique(out)

    # RULE: the placeholders are filled in for each repository
    return replace(
        rules, allow=filled(rules.allow), deny=filled(rules.deny), ask=filled(rules.ask), retired=filled(rules.retired)
    )


def waived(rules: Rules, waiver: waivers.Waiver | None) -> Rules:
    """The rules for one folder, with the asks its waiver keeps out marked; unchanged without one."""
    return replace(rules, waived=waiver.asks if waiver else ())


def folders(cfg: Config) -> list[Path]:
    """The bridge's clones, the phone app's and each plugin's, from its settings: those that exist, once each."""
    paths = cfg.paths
    named = [paths.claude_voice, paths.claude_voice_ui, paths.claude_voice_mobile, *(Path(p.cwd) for p in cfg.plugins)]
    found = {path.resolve(): None for path in named if path is not None and path.is_dir()}
    return list(found)


HOOK_MARK = "session_hook.py"


def hook_command(repos: list[str], terms: Path | None, python: str = "python3") -> str:
    """The shell command Claude Code runs before each shell command: the session hook, with
    the standard library only, told the programme's repositories and the owner's terms file."""
    script = Path(__file__).with_name(HOOK_MARK).resolve()
    parts = [python, "-I", str(script), "--repos", ",".join(sorted(set(repos)))]
    if terms is not None:
        parts += ["--terms", str(terms)]
    return " ".join(shlex.quote(part) for part in parts)


def written_hook(settings: dict[str, Any]) -> str | None:
    """The session hook's command in these settings, if they have one."""
    for entry in (settings.get("hooks") or {}).get("PreToolUse") or []:
        for hook in entry.get("hooks") or []:
            if HOOK_MARK in str(hook.get("command", "")):
                return str(hook["command"])
    return None


def _without_hook(entry: dict[str, Any]) -> dict[str, Any] | None:
    """One PreToolUse entry less the session hook, or None when nothing else was in it."""
    rest = [h for h in entry.get("hooks") or [] if HOOK_MARK not in str(h.get("command", ""))]
    return {**entry, "hooks": rest} if rest else None


def _with_hook(settings: dict[str, Any], command: str) -> dict[str, Any]:
    """The settings with the session hook as their one session hook, every other hook kept."""
    hooks = dict(settings.get("hooks") or {})
    if not isinstance(hooks, dict):
        raise PermissionsError("its hooks are not a table")
    kept = [e for e in map(_without_hook, hooks.get("PreToolUse") or []) if e is not None]
    # RULE: the session hook is written once beside the rules and every other hook is kept
    kept.append({"matcher": "Bash", "hooks": [{"type": "command", "command": command}]})
    return {**settings, "hooks": {**hooks, "PreToolUse": kept}}


def merged(settings: dict[str, Any], rules: Rules, hook: str | None = None) -> dict[str, Any]:
    """The settings with the rules added after the owner's own, each rule once, and none retired;
    and the session hook, when one is given."""
    have = settings.get("permissions") or {}
    if not isinstance(have, dict):
        raise PermissionsError("its permissions are not a table of rules")
    out = dict(have)
    for key, wanted in (("allow", rules.allow), ("ask", _wanted_asks(rules)), ("deny", rules.deny)):
        if key in have or wanted:
            out[key] = _merged_list(have, key, wanted, rules)
    result = {**settings, "permissions": out}
    return _with_hook(result, hook) if hook else result


def _wanted_asks(rules: Rules) -> list[str]:
    """The ask rules to add: every one but those the owner's waiver keeps out of this folder."""
    # RULE: a waived ask is not added to its folder
    return [rule for rule in rules.ask if rule not in rules.waived]


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
    #: Whether the session hook was written or changed.
    hook: bool = False
    #: What the owner's waiver kept out of this folder, and why; said on every run.
    kept_out: str = ""

    def line(self) -> str:
        # RULE: every run says what the owners waiver kept out and why
        return self._changed() + (f"; {self.kept_out}" if self.kept_out else "")

    def _changed(self) -> str:
        asks = f", {self.added_ask} ask" if self.added_ask else ""
        counts = f"{self.added_allow} allow{asks} and {self.added_deny} deny rules"
        if not any((self.added_allow, self.added_ask, self.added_deny, self.removed_allow, self.removed_ask)):
            return self._only_the_hook()
        retired = self._retired_note()
        if self.dry_run:
            return f"{self.folder}: would add {counts}{retired}"
        notes = [f"created {SETTINGS}"] if self.created else []
        notes += [f"kept the old file as {self.backup}"] if self.backup else []
        return f"{self.folder}: added {counts}{retired}" + "".join(f"; {note}" for note in notes)

    def _only_the_hook(self) -> str:
        """The line when no rule changed: the session hook was written, or nothing was."""
        if not self.hook:
            return f"{self.folder}: already has every rule"
        return f"{self.folder}: {'would write' if self.dry_run else 'wrote'} the session hook"

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
    hook: str | None = None,
    waiver: waivers.Waiver | None = None,
) -> Outcome:
    """Add the rules to one repository's local settings, or say what that would change.

    Before a change the old file is copied into ``backup_root``, in a folder named for the
    repository, never into the repository itself. The asks the owner's waiver names for this
    folder are left out, and the line says so."""
    if not folder.is_dir():
        raise PermissionsError(f"{folder} is not a folder, so it was left alone")
    file = folder / SETTINGS
    before = _read(file)
    here = for_folder(waived(rules, waiver), folder.resolve())
    try:
        after = merged(before, here, hook)
    except PermissionsError as exc:
        raise PermissionsError(f"{file}: {exc}, so it was left alone") from exc
    counts = replace(
        _changes(folder, before, after),
        hook=written_hook(before) != written_hook(after),
        kept_out=_kept_out(waiver, after, dry_run) if waiver else "",
    )
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


def _kept_out(waiver: waivers.Waiver, settings: dict[str, Any], dry_run: bool) -> str:
    """The waiver's words for one run; a dry run adds what then decides each waived command."""
    said = waiver.line(dry_run)
    if not dry_run:
        return said
    # RULE: a dry run says what decides a waived command once its ask is gone
    return said + "".join(f"; {_decided_by(settings, ask)}" for ask in waiver.asks)


def _decided_by(settings: dict[str, Any], ask: str) -> str:
    """What decides a waived ask's own command in these settings: in Claude Code's order the
    first rule that matches it, else the session hook, else Claude Code's own question."""
    command = ask[len("Bash(") : -1].replace("*", "").strip()
    rule = _deciding_rule(settings.get("permissions") or {}, command)
    if rule:
        what = rule
    elif written_hook(settings):
        what = "your session hook"
    else:
        what = "Claude Code asks as it would without the rule"
    return f"`{command}` is then decided by: {what}"


def _deciding_rule(have: dict[str, Any], command: str) -> str:
    """The first rule that decides a command in Claude Code's order, named with its kind; empty
    when no rule matches it."""
    lists = {kind: list(have.get(kind) or []) for kind in ("allow", "ask", "deny")}
    here = Rules(allow=lists["allow"], deny=lists["deny"], ask=lists["ask"])
    kind = {"refused": "deny", "asked": "ask", "allowed": "allow"}[decision(here, command)]
    rule = next((r for r in lists[kind] if matches(r, command)), None)
    return f"the {kind} rule {rule}" if rule else ""
