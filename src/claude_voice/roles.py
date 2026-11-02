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


def guard_settings(role: dict[str, Any]) -> str | None:
    """Claude Code settings that run the push guard before every shell command, if the role asks."""
    upstream = str(role.get("upstream") or "")
    if not role.get("guard") or not upstream:
        return None
    command = f"{shlex.quote(sys.executable)} -m claude_voice.guard --upstream {shlex.quote(upstream)}"
    return json.dumps(
        {"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": command}]}]}}
    )


def public(name: str, role: dict[str, Any]) -> dict[str, Any]:
    """What a client may see of a role."""
    return {"name": name, "title": role.get("title") or name, "description": role.get("description") or ""}
