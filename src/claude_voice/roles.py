"""Roles: background sessions started for a set purpose, such as contributing back.

The built-in roles live in ``roles.toml`` beside this file. A user's own file is
laid over them key by key, so a user can rename a role's session, point it at
another clone, or add roles of their own.
"""

from __future__ import annotations

import json
import shlex
import sys
import tomllib
from pathlib import Path
from typing import Any

BUILT_IN = Path(__file__).with_name("roles.toml")
USER_FILE = Path("~/.config/claude-voice/roles.toml")
WAITING = "Wait for the owner to say what they want changed."
#: The organisation the bridge's own roles belong to, unless a role names another.
ORG = "voice"
#: The fingerprints of the installed rule packs; only the owner's install writes it.
PINS = Path("~/.config/claude-voice/orgs/pins.json").expanduser()
#: The packs and their install records, refused to every role's writes (org-rules v1.2 §2).
PACK_FOLDERS = (".config/claude-voice/orgs/**", ".local/state/claude-voice/orgs/**")
PERMISSIONS = Path(__file__).with_name("permissions.json")


def load(user_file: Path | None, defaults: dict[str, Any] | None = None) -> dict[str, dict[str, Any]]:
    """Every role by name: the built-in ones, with the user's file laid over them. A role's
    empty `folder` or `upstream` is taken from `defaults` (the repository settings)."""
    with BUILT_IN.open("rb") as f:
        found: dict[str, dict[str, Any]] = {k: dict(v) for k, v in tomllib.load(f).get("roles", {}).items()}
    path = user_file.expanduser() if user_file else None
    if path and path.is_file():
        with path.open("rb") as f:
            for name, values in tomllib.load(f).get("roles", {}).items():
                found[name] = {**found.get(name, {}), **values}
    from .config import UPSTREAM

    fill = {"upstream": UPSTREAM, **{k: v for k, v in (defaults or {}).items() if v}}
    for role in found.values():
        for key, value in fill.items():
            if not role.get(key):
                role[key] = str(value)
    return found


def own_clone() -> Path | None:
    """The git checkout this bridge runs from, if it runs from one."""
    root = Path(__file__).resolve().parents[2]
    return root if (root / ".git").exists() else None


def folder_for(role: dict[str, Any]) -> Path:
    folder = str(role.get("folder") or "").strip()
    path = Path(folder).expanduser() if folder else own_clone()
    if path is None or not path.is_dir():
        raise LookupError(
            f"No folder to work in for this role: set `folder` in {USER_FILE} to your clone of the project."
        )
    return path


def first_message(role: dict[str, Any], folder: Path, task: str | None) -> str:
    template = str(role.get("prompt") or "{task}")
    text = template.replace("{folder}", str(folder)).replace("{upstream}", str(role.get("upstream", "")))
    return text.replace("{task}", task.strip() if task and task.strip() else WAITING).strip()


def _guard(role: dict[str, Any]) -> dict[str, Any]:
    upstream = str(role.get("upstream") or "")
    if not role.get("guard") or not upstream:
        return {}
    # RULE: the push guard runs isolated from the sessions folder
    command = f"{shlex.quote(sys.executable)} -I -m claude_voice.guard --upstream {shlex.quote(upstream)}"
    return {"PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": command}]}]}


def guard_settings(role: dict[str, Any]) -> str | None:
    """Claude Code settings that run the push guard before every shell command, if the role asks."""
    hooks = _guard(role)
    return json.dumps({"hooks": hooks}) if hooks else None


def _pins(path: Path, org: str) -> tuple[str, str] | None:
    """The installed shared and organisation pack shas, or None before both are installed."""
    try:
        found = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(found, dict):
        return None
    shared, own = found.get("_shared"), found.get(org)
    # RULE: before the packs are installed a lane gets no hook
    return (shared, own) if isinstance(shared, str) and isinstance(own, str) else None


def _rules_hooks(org: str, pinned: tuple[str, str]) -> dict[str, Any]:
    from .org_rules import COMMAND  # the bridge's own Python, run isolated from the session

    def hook(event: str) -> list[dict[str, Any]]:
        words = [*COMMAND, "emit", org, "--event", event]
        command = shlex.join([*words, "--shared-sha", pinned[0], "--sha", pinned[1]])
        return [{"matcher": "*", "hooks": [{"type": "command", "command": command}]}]

    return {event: hook(event) for event in ("SessionStart", "SubagentStart")}


def _pack_denies(home: Path) -> list[str]:
    """Edit, Write and the shell's writing commands, refused on the packs and their records."""
    from . import owner_rules

    with PERMISSIONS.open(encoding="utf-8") as f:
        writers = json.load(f)["what later runs as the owner"]["writers"]
    rules = owner_rules.runs_as_owner({"home": list(PACK_FOLDERS), "writers": writers})
    return [rule.replace("{home}", str(home)) for rule in rules]


def role_settings(role: dict[str, Any], pins_file: Path = PINS, home: Path | None = None) -> str:
    """The `--settings` a role starts with: the push guard if it asks for one; for a role
    that is not a manager, the hooks that read it its rules once the packs are installed;
    and for every role, no writes to the packs."""
    hooks = _guard(role)
    org = str(role.get("org") or ORG)
    pinned = _pins(pins_file, org)
    # RULE: a manager is not told the lanes rules
    if pinned is not None and not role.get("manager"):
        hooks = {**hooks, **_rules_hooks(org, pinned)}
    # RULE: no role may write the packs or their records
    settings: dict[str, Any] = {"permissions": {"deny": _pack_denies(home or Path.home())}}
    return json.dumps({**settings, "hooks": hooks} if hooks else settings)


def public(name: str, role: dict[str, Any]) -> dict[str, Any]:
    """What a client may see of a role."""
    return {"name": name, "title": role.get("title") or name, "description": role.get("description") or ""}
