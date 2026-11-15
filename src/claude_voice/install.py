"""claude-voice plugins install: add a plugin from its local checkout in one go.

It reads the manifest the plugin ships (docs/plugin-manifest.md) and does the steps that
were done by hand, numbered as it goes: the plugin's entry in the bridge's settings, the
shared home position when the plugin uses it, and the plugin's own config, written from
the default it ships with the values only the owner can give, asked without showing them.

Every check that can refuse is made before anything is written. Each file it changes is
backed up first, and the summary says how to undo each one. A dry run shows each file's
difference, personal values masked, and writes nothing. A second run changes nothing. It
never fetches anything: a missing checkout gets the clone command, for the owner to run.
The typed approval at the end is plugins approve's own, made by the command line.
"""

from __future__ import annotations

import copy
import difflib
import tomllib
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import tomli_w

from . import config as settings
from . import fields, gitstatus, safefile
from . import home as homes
from . import manifest as manifests
from .manifest import Manifest

KNOWN = Path(__file__).with_name("known_plugins.toml")
MASK = "•••"  # a personal value in a dry run
MASKED_HOME = "lat = ••.••\nlon = ••.••\n"


class InstallError(ValueError):
    """Why install will not go on; the message says what to do next."""


@dataclass(frozen=True)
class Change:
    """One thing install did, and the command that undoes it."""

    kind: str  # new, changed or approved
    what: str
    undo: str


@dataclass(frozen=True)
class Asker:
    """How install talks to the owner: lines out, hidden answers in, and plain answers in."""

    say: Callable[[str], None]
    secret: Callable[[str], str]
    line: Callable[[str], str]


@dataclass(frozen=True)
class Plan:
    """Everything install needs, checked before anything is written."""

    home: Path
    folder: Path
    manifest: Manifest
    bridge: Path
    plugin_config: Path | None
    default: dict[str, Any] | None
    known_folder: bool


@dataclass
class Outcome:
    changes: list[Change] = field(default_factory=list)
    steps: int = 0
    stopped: bool = False
    error: str | None = None


@dataclass(frozen=True)
class Known:
    """One of the owner's own plugins: its repository, and the folder its checkout had
    before the repository was renamed, if it was."""

    repo: str
    formerly: str | None = None

    @property
    def folder(self) -> str:
        return self.repo.split("/")[1]


def known() -> dict[str, Known]:
    """The owner's own plugins, by name."""
    raw = tomllib.loads(KNOWN.read_text(encoding="utf-8"))
    return {name: Known(str(body["repo"]), body.get("formerly")) for name, body in raw.items()}


def known_folders() -> set[str]:
    """The config folders the known plugins use: each one's name and its repository's,
    old and new."""
    return {*known(), *(k.folder for k in known().values()), *(k.formerly for k in known().values() if k.formerly)}


def _expand(text: str, home: Path) -> Path:
    return (home / text[2:] if text.startswith("~/") else Path(text)).absolute()


# -- what is checked before anything is written ---------------------------------------------


def plan(name: str, source: str | None, env: Mapping[str, str]) -> Plan:
    """Read and check everything install will need, or InstallError saying what is wrong."""
    home = Path(env.get("HOME") or Path.home())
    try:
        folder = _checkout(name, source, home)
        found = manifests.read(folder)
        if found.name != name:
            raise InstallError(f"{folder} holds the plugin {found.name}, not {name}. Install it as {found.name}")
        _clean(folder, found.branch, home)
        bridge = settings.load(settings.FILE, env=env).file
        assert bridge is not None  # a path is always given
        safefile.target(bridge)  # a bridge config that links to anything but a file of yours is refused now
        if "home" in found.uses:
            homes.path(home)  # a symlink or a repository on the way to the home file is refused now
        target, default, folder_known = _plugin_config(found, folder, home)
    except (manifests.ManifestError, settings.ConfigError, safefile.Unsafe) as exc:
        raise InstallError(str(exc)) from None
    return Plan(home, folder, found, bridge, target, default, folder_known)


def _checkout(name: str, source: str | None, home: Path) -> Path:
    """The plugin's checkout, which must exist: install never fetches anything."""
    found = known().get(name)
    if source is None and found is None:
        raise InstallError(f"{name} is not a known plugin; say where its checkout is with --from <folder>")
    folder = _expand(source, home) if source else _default_folder(home, found)
    if folder.is_dir():
        return folder
    shown = safefile.shown(folder, home)
    if found is None:
        raise InstallError(f"{shown} does not exist. Clone the plugin's repository there, then run this again")
    raise InstallError(
        f"{shown} does not exist. Clone it there yourself, then run this again:\n"
        f"  git clone https://github.com/{found.repo} {shown}"
    )


def _default_folder(home: Path, found: Known | None) -> Path:
    """Where a known plugin's checkout is: its repository's name, or the name it had."""
    assert found is not None
    old = home / found.formerly if found.formerly else None
    # RULE: the old checkout folder of a renamed plugin is still found
    if old is not None and old.is_dir() and not (home / found.folder).exists():
        return old
    return home / found.folder


def _clean(folder: Path, branch: str, home: Path) -> None:
    status = gitstatus.read(folder)
    why = _unclean(status, branch)
    # RULE: a plugin checkout that is not clean or not on its branch is refused
    if why:
        raise InstallError(f"{safefile.shown(folder, home)} {why}; plugins approve would refuse it too")


def _unclean(status: dict[str, Any] | None, branch: str) -> str | None:
    if status is None:
        return "is not a git checkout"
    if status.get("dirty"):
        return "has changes that are not committed; commit them or remove them (git status lists them)"
    if status.get("branch") != branch:
        return f"is not on {branch}, the branch its manifest follows; run git switch {branch} in it"
    return None


def _plugin_config(found: Manifest, folder: Path, home: Path) -> tuple[Path | None, dict[str, Any] | None, bool]:
    """Where the plugin's config goes and the default it starts from, both confined."""
    if found.config is None:
        return None, None, True
    where = found.config.dir
    named = known().get(found.name)
    allowed = {found.name, folder.name, *([named.folder] if named else [])}  # a renamed checkout keeps its name
    # RULE: a config folder that is neither the plugin nor its checkout is refused
    if where not in allowed:
        raise InstallError(
            f"[config] dir = {where!r} must be the plugin's name ({found.name}) or its checkout folder's "
            f"({folder.name}); fix the plugin's manifest"
        )
    target = safefile.outside_repositories(safefile.inside(home / ".config", f"{where}/config.toml"))
    source = safefile.inside(folder, found.config.default)
    try:
        default = tomllib.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise InstallError(f"the plugin's default config {source} could not be read ({exc})") from None
    return target, default, where in known_folders()


# -- the steps ------------------------------------------------------------------------------


def entry(found: Manifest, folder: Path, home: Path) -> dict[str, Any]:
    """The plugin's [plugins.<name>] table."""
    cwd = f"~/{folder.relative_to(home)}" if folder.is_relative_to(home) else str(folder)
    return {
        "command": found.command,
        "args": list(found.args),
        "cwd": cwd,
        "tools": list(found.tools),
        "timeout": found.timeout,
        "branch": found.branch,
    }


def added(old: str, name: str, table: dict[str, Any]) -> str:
    """The bridge config with the new table at its end, serialized rather than pasted."""
    if not old:
        return tomli_w.dumps({"plugins": {name: table}})
    return old + ("" if old.endswith("\n") else "\n") + "\n" + tomli_w.dumps({"plugins": {name: table}})


def config_doc(default: dict[str, Any], answers: dict[str, Any], home_file: bool) -> dict[str, Any]:
    """The plugin's config: its default, home_file when it uses home, and the answers."""
    doc = copy.deepcopy(default)
    if home_file:
        doc["home_file"] = homes.SHOWN
    for key, value in answers.items():
        *parents, last = key.split(".")
        table = doc
        for part in parents:
            table = table.setdefault(part, {})
            if not isinstance(table, dict):
                raise InstallError(f"the default config has {part} as a value, so {key} cannot go under it")
        table[last] = value
    return doc


class Run:
    """One install, step by step."""

    def __init__(self, plan: Plan, asker: Asker, dry: bool, anywhere: bool, now: datetime) -> None:
        self.plan, self.asker, self.dry, self.anywhere, self.now = plan, asker, dry, anywhere, now
        self.outcome = Outcome()
        self.name = plan.manifest.name

    def step(self, text: str) -> None:
        self.outcome.steps += 1
        self.asker.say(f"{self.outcome.steps}. {text}")

    def note(self, text: str) -> None:
        self.asker.say(f"   {text}")

    def shown(self, path: Path) -> str:
        return safefile.shown(path, self.plan.home)

    def diff(self, before: str, after: str, old: str, new: str) -> None:
        for line in difflib.unified_diff(old.splitlines(), new.splitlines(), before, after, lineterm=""):
            self.note(line)

    def write(self, path: Path, old: str | None, new: str, narrow: bool = True) -> None:
        """Write one file, backing up the old one; in a dry run, show the difference instead."""
        shown = self.shown(path)
        # RULE: an install dry run writes nothing
        if self.dry:
            self.diff("/dev/null" if old is None else shown, shown, old or "", new)
            return
        if safefile.private_folder(path.parent, narrow):
            self.note(f"narrowed {self.shown(path.parent)} to owner-only (0700); others could list it.")
        if old is None:
            safefile.replace(path, new)
            self.outcome.changes.append(Change("new", shown, f"rm {shown}"))
            return
        spare = safefile.backup(path, self.now)
        safefile.replace(path, new)
        self.note(f"backup: {self.shown(spare)}")
        self.outcome.changes.append(Change("changed", shown, f"cp {self.shown(spare)} {shown}"))

    def manifest_step(self) -> None:
        found = self.plan.manifest
        source = self.shown(self.plan.folder / manifests.FILE)
        self.step(f"Read the manifest: {source} ({found.name}, {len(found.tools)} tools).")

    def bridge_step(self) -> None:
        path, found = self.plan.bridge, self.plan.manifest
        old = path.read_text(encoding="utf-8") if path.exists() else None
        self.step(f"Bridge config: {self.shown(path)}")
        raw = tomllib.loads(old or "")
        table = entry(found, self.plan.folder, self.plan.home)
        there = (raw.get("plugins") or {}).get(self.name) if isinstance(raw.get("plugins"), dict) else None
        if there is not None:
            self._same(there, table)
            return
        tools = f"{len(found.tools)} tools"
        self.note(f"add [plugins.{self.name}] (command {found.command}, {tools}, timeout {found.timeout:g} s)")
        new = added(old or "", self.name, table)
        self._checked(raw, new, table)
        self.write(path, old, new, narrow=False)  # the bridge's own folder is left as it is

    def _same(self, there: Any, table: dict[str, Any]) -> None:
        home = str(self.plan.home)
        try:
            same = settings.plugin_spec(self.name, there, home) == settings.plugin_spec(self.name, table, home)
        except settings.ConfigError:
            same = False
        # RULE: a different plugin entry is never rewritten
        if same:
            self.note("Already in your config.")
            return
        mine = tomli_w.dumps(there) if isinstance(there, dict) else repr(there)
        self.diff(f"[plugins.{self.name}] in your config", "what install would add", mine, tomli_w.dumps(table))
        # RULE: the stop for a different entry names the entry and how to compare
        entry = f"[plugins.{self.name}]"
        again = f"claude-voice plugins install {self.name} --from {self.shown(self.plan.folder)} --dry-run"
        raise InstallError(
            f"{self.shown(self.plan.bridge)} already has a different {entry}, so it is left as it is. Change its "
            f"- lines to the + lines by hand, or remove it, then run this again. To compare them again: {again}"
        )

    def _checked(self, raw: dict[str, Any], new: str, table: dict[str, Any]) -> None:
        expected = copy.deepcopy(raw)
        expected.setdefault("plugins", {})[self.name] = table
        try:
            parsed: dict[str, Any] | None = tomllib.loads(new)
        except tomllib.TOMLDecodeError:
            parsed = None
        # RULE: the new bridge config must parse as the old one plus exactly the new entry
        if parsed != expected:
            raise InstallError(
                f"adding the entry would change more of {self.shown(self.plan.bridge)} than the entry, so it is "
                f"left as it is. Add this to it by hand:\n\n{tomli_w.dumps({'plugins': {self.name: table}})}"
            )

    def home_step(self) -> None:
        if "home" not in self.plan.manifest.uses:
            return
        target = homes.path(self.plan.home)
        self.step(f"Home position: {self.shown(target)}")
        # RULE: an existing home position is kept and not asked again
        if target.exists():
            self.note("Using your home position.")
            return
        self.note("does not exist yet.")
        if self.dry:
            self.write(target, None, MASKED_HOME)
            self.note("would ask for latitude and longitude, not shown")
            return
        position = homes.ask(self.asker.secret, self.asker.say, self.anywhere)
        self.write(target, None, homes.text(position))
        self.note("saved, rounded to 2 decimals.")

    def config_step(self) -> None:
        found, target = self.plan.manifest, self.plan.plugin_config
        if found.config is None or target is None or self.plan.default is None:
            return
        shown = self.shown(target)
        self.step(f"Plugin config: {shown}")
        # RULE: an existing plugin config is kept
        if target.exists():
            self.note(f"kept your config at {shown}.")
            self._home_line(target)
            return
        uses_home = "home" in found.uses
        with_home = ", with home_file" if uses_home else ""
        answers = _answers(len(found.config.personal))
        self.note(f"does not exist yet; written from {found.config.default}{with_home} and {answers}.")
        if not self._folder_agreed(shown):
            raise InstallError(f"{shown} was not written, since you did not say yes. Run this again to write it")
        given = {p.key: self._answer(p) for p in found.config.personal}
        self.write(target, None, tomli_w.dumps(config_doc(self.plan.default, given, uses_home)))

    def _answer(self, personal: manifests.Personal) -> Any:
        if self.dry:
            return MASK
        prompt = f"   {personal.ask} ({personal.field.fits()}; not shown): "
        return fields.ask(personal.field, prompt, self.asker.secret, self.asker.say)

    def _folder_agreed(self, shown: str) -> bool:
        if self.plan.known_folder:
            return True
        if self.dry:
            self.note(f"would ask before writing it, since no known plugin uses {shown}")
            return True
        question = f"   No known plugin uses {shown}; write it here? [y/N] "
        # RULE: a config folder no known plugin uses is written only on a yes
        return self.asker.line(question).strip().lower() in ("y", "yes")

    def _home_line(self, target: Path) -> None:
        """Say so when a kept config lacks home_file, so the plugin would not find home."""
        if "home" not in self.plan.manifest.uses:
            return
        try:
            kept = tomllib.loads(target.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
            self.note("it could not be read as TOML, so whether it names home_file is not known.")
            return
        if "home_file" not in kept:
            self.note("It has no home_file, so the plugin cannot find the shared home position. Add this line")
            self.note(f'at its top, before any [table]: home_file = "{homes.SHOWN}"')


def _answers(count: int) -> str:
    return "1 answer" if count == 1 else f"{count} answers"


def run(plan: Plan, asker: Asker, dry: bool = False, anywhere: bool = False, now: datetime | None = None) -> Outcome:
    """Do the install's steps in order; what it did, and whether it stopped before the end."""
    steps = Run(plan, asker, dry, anywhere, now or datetime.now())
    try:
        steps.manifest_step()
        steps.bridge_step()
        steps.home_step()
        steps.config_step()
    except (InstallError, safefile.Unsafe) as exc:
        steps.outcome.stopped, steps.outcome.error = True, str(exc)
    except (EOFError, KeyboardInterrupt):
        asker.say("")
        steps.outcome.stopped = True
    return steps.outcome


def summary(outcome: Outcome, plan: Plan, dry: bool) -> list[str]:
    """What changed and the exact undo for each, never a personal value."""
    if dry:
        return ["", "Dry run: nothing was written."]
    lines = ["", "Stopped." if outcome.stopped else "Done."]
    if not outcome.changes:
        lines.append("  Nothing was changed." if outcome.stopped else "  Nothing changed: it was all in place already.")
    width = max((len(c.what) for c in outcome.changes), default=0)
    lines += [f"  {c.kind:<8} {c.what:<{width}}  undo: {c.undo}" for c in outcome.changes]
    if plan.manifest.secrets_step and not outcome.stopped:
        lines.append(
            f"  next     {plan.manifest.secrets_step}  (the plugin's own step for its key; install asks for none)"
        )
    return lines
