"""The bridge's own commands for its settings and roles files (runs-as-owner v1.3 §6.1).

A session may not edit these files directly, because what a plugin or role runs with runs
later as the owner. These commands are the sanctioned way to change them. They write only
keys that aren't run, or a run key set back to exactly what the owner approved by typing.

Each write keeps the rest of the file, its comments and order included, with a dated backup
beside it first.
"""

from __future__ import annotations

import sys
import tomllib
from collections.abc import Callable, Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

import tomlkit
from tomlkit.exceptions import TOMLKitError

from . import plugins, safefile
from .config import plugin_spec

#: A plugin's keys that change what runs, and so need the owner's typed approval.
RUN_KEYS = frozenset({"command", "args", "cwd", "env", "terminal_command", "branch"})
#: The run keys the approval's fingerprint covers; the others are never set by command.
APPROVED_KEYS = frozenset({"command", "args", "cwd"})
#: The top-level settings `claude-voice set` may write: text read by the voice, not run.
TOP_KEYS = frozenset({"voice_instructions_file"})
#: A role's keys that would give it a start line of its own; it comes from the template only.
ROLE_START = frozenset({"terminal_command", "command"})


#: Role fields that set up the push guard in that role's sessions: only switched on, never off.
ROLE_GUARD = frozenset({"guard", "upstream"})


def _check_role_fields(fields: Mapping[str, Any]) -> None:
    if ROLE_START.intersection(fields):
        raise Refused("a role's start line comes only from the template; leave out terminal_command")
    # RULE: roles add never switches the push guard off or points it elsewhere
    if "upstream" in fields or fields.get("guard", True) is not True:
        raise Refused("a role's push guard is the owner's to loosen; only guard=true can be set by command")


class Refused(ValueError):
    """A change these commands will not make, with the reason in words."""


def _load(path: Path) -> dict[str, Any]:
    try:
        return tomlkit.parse(path.read_text(encoding="utf-8")) if path.is_file() else tomlkit.document()
    except TOMLKitError as exc:
        raise Refused(f"{path} is not valid TOML: {exc}") from exc


def _save(path: Path, raw: dict[str, Any], now: datetime | None = None) -> None:
    if path.is_file():
        safefile.backup(path, now or datetime.now())
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
    safefile.replace(path, tomlkit.dumps(raw))  # tomlkit keeps the owner's comments and order


def value_of(text: str) -> Any:
    """A command-line value as TOML reads it (a list, a number, a quoted string), or else as
    plain text."""
    try:
        return tomllib.loads(f"v = {text}")["v"]
    except tomllib.TOMLDecodeError:
        return text


def set_plugin(config: Path, approvals: Path | None, name: str, key: str, text: str, *, home: Path) -> None:
    """Set one key of a plugin already in the settings file."""
    raw = _load(config)
    table = raw.get("plugins", {}).get(name)
    if not isinstance(table, dict):
        raise Refused(f"no plugin {name!r} in {config}; add one with claude-voice plugins install")
    value = value_of(text)
    if key in RUN_KEYS and table.get(key) != value:
        _check_run_key(name, {**table, key: value}, key, approvals, home)
    table[key] = value
    _save(config, raw)


def _check_run_key(name: str, table: dict[str, Any], key: str, approvals: Path | None, home: Path) -> None:
    # RULE: plugins set refuses each key a plugin runs with unless the owner approved it
    if key not in APPROVED_KEYS:
        raise Refused(f"{key} changes what {name} runs; only the owner changes it")
    if not plugins.approved(plugin_spec(name, table, str(home)), approvals):
        raise Refused(f"{key} would make {name} run other than the owner approved; ask the owner")


def add_machine(config: Path, name: str, address: str) -> None:
    """Add a machine by its name and address."""
    raw = _load(config)
    machines = raw.setdefault("machines", {})
    if name in machines:
        raise Refused(f"a machine named {name!r} is already in {config}")
    machines[name] = {"address": address}
    _save(config, raw)


def set_top(config: Path, key: str, text: str) -> None:
    """Set one of the few top-level settings that are not run."""
    if key not in TOP_KEYS:
        raise Refused(f"{key} can't be set by command; these can: {', '.join(sorted(TOP_KEYS))}")
    raw = _load(config)
    raw[key] = value_of(text)
    _save(config, raw)


def add_role(roles: Path, name: str, fields: dict[str, Any]) -> None:
    """Add a role of the owner's own, with no start line of its own."""
    # RULE: roles add refuses a start line of its own
    _check_role_fields(fields)
    raw = _load(roles)
    found = raw.setdefault("roles", {})
    if name in found:
        raise Refused(f"a role named {name!r} is already in {roles}")
    found[name] = fields
    _save(roles, raw)


def files(env: Mapping[str, str]) -> tuple[Path, Path, Path]:
    """The settings file, the plugin approvals and the owner's roles file, under env's home."""
    from . import config, roles

    home = Path(env.get("HOME") or Path.home())
    named = env.get("CLAUDE_VOICE_CONFIG")
    setting = Path(named).expanduser() if named else home / config.FILE.relative_to("~")
    return setting, home / plugins.APPROVALS.relative_to("~"), home / roles.USER_FILE.relative_to("~")


def command(verb: str, words: list[str], env: Mapping[str, str]) -> int:
    """Run one of the commands from the command line: what it changed, or why not, in words."""
    setting, approvals, owned_roles = files(env)
    home = Path(env.get("HOME") or Path.home())
    steps: dict[str, tuple[int, Callable[[], None]]] = {
        "plugins set": (3, lambda: set_plugin(setting, approvals, *words[:3], home=home)),
        "machines add": (2, lambda: add_machine(setting, *words[:2])),
        "set": (2, lambda: set_top(setting, *words[:2])),
        "roles add": (1, lambda: add_role(owned_roles, words[0], _fields(words[1:]))),
        "roles set": (3, lambda: set_role(owned_roles, *words[:3])),
        "roles remove": (1, lambda: remove_role(owned_roles, words[0])),
    }
    needs, step = steps[verb]
    try:
        if len(words) < needs:
            raise Refused(f"{verb} needs {needs} words after it, such as the examples in claude-voice --help")
        step()
    except (Refused, safefile.Unsafe, OSError) as exc:
        sys.stderr.write(f"claude-voice: {verb}: {exc}\n")
        return 1
    sys.stdout.write(f"claude-voice: {verb} {' '.join(words[:1])}: written, with a backup beside the file\n")
    return 0


def _fields(words: list[str]) -> dict[str, Any]:
    pairs = [w.partition("=") for w in words]
    if any(not eq for _, eq, _ in pairs):
        raise Refused("give a role's fields as key=value, such as folder=~/demo")
    return {key: value_of(value) for key, _, value in pairs}


def set_role(roles: Path, name: str, key: str, text: str) -> None:
    """Change one field of a role: one of the owner's own, or a built-in one, laid over in the
    owner's file. A start line is never set this way."""
    from . import roles as role_files

    _check_role_fields({key: value_of(text)})
    raw = _load(roles)
    found = raw.setdefault("roles", {})
    if name not in found and name not in role_files.load(None):
        raise Refused(f"no role named {name!r}; add one with claude-voice roles add")
    found.setdefault(name, {})[key] = value_of(text)
    _save(roles, raw)


def remove_role(roles: Path, name: str) -> None:
    """Take one of the owner's own roles out of the roles file."""
    raw = _load(roles)
    if name not in raw.get("roles", {}):
        raise Refused(f"no role named {name!r} in {roles}")
    _keep_the_notes_after(raw["roles"], name)
    del raw["roles"][name]
    _save(roles, raw)


def _keep_the_notes_after(tables: Any, name: str) -> None:
    """tomlkit files a comment written above a table under the table before it. Before that
    table goes, its trailing comments and blank lines move to above the next one."""
    body = getattr(tables[name], "value", None)
    if body is None or not hasattr(body, "body"):
        return
    keyed = [i for i, (key, _) in enumerate(body.body) if key is not None]
    trailing = body.body[keyed[-1] + 1 :] if keyed else body.body
    notes = "".join(item.as_string() for _, item in trailing)
    names = list(tables)
    after = names[names.index(name) + 1 :]
    if notes and after:
        following = tables[after[0]]
        following.trivia.indent = notes + following.trivia.indent
