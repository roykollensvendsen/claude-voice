"""Roles: background sessions started for a set purpose, such as contributing back.

The built-in roles live in ``roles.toml`` beside this file. A user's own file is
laid over them key by key, so a user can rename a role's session, point it at
another clone, or add roles of their own.
"""

from __future__ import annotations

import hashlib
import json
import logging
import shlex
import sys
import tomllib
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

BUILT_IN = Path(__file__).with_name("roles.toml")
USER_FILE = Path("~/.config/claude-voice/roles.toml")
#: The only folder a role's own settings file may live in.
SETTINGS_DIR = Path("~/.local/state/claude-voice/roles")
#: Settings that would let a role's session skip the checks, never accepted from a role's file.
SKIPS = {"defaultMode": ("bypassPermissions", "acceptEdits")}
WAITING = "Wait for the owner to say what they want changed."


def load(user_file: Path | None, defaults: dict[str, Any] | None = None) -> dict[str, dict[str, Any]]:
    """Every role by name: the built-in ones, with the user's file laid over them. A role's
    empty `folder` or `upstream` is taken from `defaults` (the repository settings)."""
    with BUILT_IN.open("rb") as f:
        found: dict[str, dict[str, Any]] = {k: dict(v) for k, v in tomllib.load(f).get("roles", {}).items()}
    for name, role in found.items():
        # RULE: a roles settings file is honoured only from the owners own roles file
        if role.pop("settings", None) is not None:
            logger.warning("the built-in role %s names a settings file; it is ignored", name)
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
    """Claude Code settings that run the push guard before every shell command, if the role asks.
    A role with its own settings file and no guard still gets the guard's check on deletes."""
    upstream = str(role.get("upstream") or "")
    only_deletes = bool(role.get("settings")) and not role.get("guard")
    if not (role.get("guard") or only_deletes) or not upstream:
        return None
    # RULE: the push guard runs isolated from the sessions folder
    command = f"{shlex.quote(sys.executable)} -I -m claude_voice.guard --upstream {shlex.quote(upstream)}"
    # RULE: own-clone mode is on only when the role entry names it exactly
    if role.get("push_mode") == "own-clone" and not only_deletes:
        command += " --push-mode own-clone"
    # RULE: a role with its own settings and no guard gets the delete check
    if only_deletes:
        command += " --push-mode no-deletes"
    return json.dumps(
        {"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": command}]}]}}
    )


def settings_for(role: dict[str, Any], name: str = "") -> str | None:
    """A role session's whole settings: the role's own file, if it names one, with the guard laid
    over it. The merge only adds: every hook and deny of either side is kept."""
    guard = guard_settings(role)
    if not role.get("settings"):
        return guard
    path, own = _read_settings(str(role["settings"]))
    # RULE: each launch logs the settings files fingerprint
    logger.info(
        "role %s starts with settings %s (sha256 %s)", name, path, hashlib.sha256(path.read_bytes()).hexdigest()
    )
    extra = json.loads(guard) if guard else {}
    hooks = {**own.get("hooks", {})}
    for event, entries in extra.get("hooks", {}).items():
        # RULE: the guard hook is kept beside the files own hooks
        hooks[event] = [*hooks.get(event, []), *entries]
    return json.dumps({**own, **({"hooks": hooks} if hooks else {})})


def _read_settings(given: str) -> tuple[Path, dict[str, Any]]:
    """The role's settings file and what it says, or LookupError naming the fix."""
    folder = SETTINGS_DIR.expanduser().resolve()
    path = Path(given).expanduser()
    if not path.is_file():
        raise LookupError(f"The role's settings file {path} is missing: put it under {folder}, or remove `settings`.")
    real = path.resolve()
    # RULE: a settings file outside the roles folder is refused
    if not real.is_relative_to(folder):
        raise LookupError(f"The role's settings file {path} is outside {folder}: move the file under {folder}.")
    try:
        own = json.loads(real.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise LookupError(f"The role's settings file {path} is not JSON ({exc}).") from None
    if not isinstance(own, dict):
        raise LookupError(f"The role's settings file {path} must hold a JSON object.")
    _refuse_skips(own, path)
    return real, own


def _refuse_skips(own: dict[str, Any], path: Path) -> None:
    # RULE: a settings file that skips checks is refused
    modes = [own.get("defaultMode"), (own.get("permissions") or {}).get("defaultMode")]
    if any(mode in SKIPS["defaultMode"] for mode in modes):
        raise LookupError(f"The role's settings file {path} skips the permission checks: remove defaultMode from it.")
    if own.get("disableAllHooks"):
        raise LookupError(f"The role's settings file {path} turns hooks off: remove disableAllHooks from it.")


def public(name: str, role: dict[str, Any]) -> dict[str, Any]:
    """What a client may see of a role."""
    return {"name": name, "title": role.get("title") or name, "description": role.get("description") or ""}
