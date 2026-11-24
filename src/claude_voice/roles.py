"""Roles: background sessions started for a set purpose, such as contributing back.

The built-in roles live in ``roles.toml`` beside this file. A user's own file is
laid over them key by key, so a user can rename a role's session, point it at
another clone, or add roles of their own.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
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
#: Permission modes that would let a role's session skip the checks, never accepted from a role's file.
SKIPS = ("bypassPermissions", "acceptEdits", "dontAsk")
#: The rule lists in a settings file's permissions.
RULE_LISTS = ("allow", "ask", "deny")
WAITING = "Wait for the owner to say what they want changed."
#: Text rules that refuse the plain spellings of a merge to a role that may not merge. The guard
#: hook is the real check: it reads gh's flags and every writing call to GitHub's API.
MERGE_DENIES = ("Bash(gh pr merge*)", "Bash(gh -R * pr merge*)", "Bash(gh --repo * pr merge*)")
#: Upstreams whose approved pull requests land in batches, merged by the manager alone
#: (ADR-CV-023). Today that is only the bridge's own.
BATCHED = ("roykollensvendsen/claude-voice",)


def denies_merges(role: dict[str, Any]) -> bool:
    """Whether a role's session is refused merges and check results on GitHub. `deny_merges = true`
    refuses them anywhere; on a batched upstream they are refused unless `merges = true`."""
    # RULE: deny_merges is honoured from either roles file
    if role.get("deny_merges") is True:
        return True
    # RULE: merges are denied by default only on an upstream that merges in batches
    return _repository(str(role.get("upstream") or "")) in BATCHED and role.get("merges") is not True


def _repository(upstream: str) -> str:
    """`owner/name` from an upstream however it is written: any case, as a URL or an ssh address,
    with or without `.git` and a trailing slash."""
    # RULE: an upstream is compared as owner and name whatever its spelling
    name = re.sub(r"^(?:[a-z+]+://[^/]+/|[^@/]+@[^:/]+:)", "", upstream.strip(), flags=re.IGNORECASE)
    return name.rstrip("/").removesuffix(".git").lower()


def load(user_file: Path | None, defaults: dict[str, Any] | None = None) -> dict[str, dict[str, Any]]:
    """Every role by name: the built-in ones, with the user's file laid over them. A role's
    empty `folder` or `upstream` is taken from `defaults` (the repository settings)."""
    found = _built_in()
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


def _built_in() -> dict[str, dict[str, Any]]:
    """The roles shipped with the bridge, without any settings file they name."""
    with BUILT_IN.open("rb") as f:
        found: dict[str, dict[str, Any]] = {k: dict(v) for k, v in tomllib.load(f).get("roles", {}).items()}
    for name, role in found.items():
        # RULE: a roles settings file is honoured only from the owners own roles file
        if role.pop("settings", None) is not None:
            logger.warning("the built-in role %s names a settings file; it is ignored", name)
        # RULE: merges is honoured only from the owners own roles file
        if role.pop("merges", None) is not None:
            logger.warning("the built-in role %s says it merges; it is ignored", name)
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
    """Claude Code settings that run the guard before every shell command of a role's session.
    A role that asks for the guard gets the push check; a role with its own settings file and no
    guard gets the guard's check on deletes; a role denied merges with neither gets only the check
    on merges, and any other role gets no hook at all. See `denies_merges` for who may merge."""
    upstream = str(role.get("upstream") or "")
    only_deletes = bool(role.get("settings")) and not role.get("guard")
    denied = denies_merges(role)
    if not upstream or not (role.get("guard") or only_deletes or denied):
        return None
    out: dict[str, Any] = {
        "hooks": {
            "PreToolUse": [
                {"matcher": "Bash", "hooks": [{"type": "command", "command": _guard_command(role, upstream)}]}
            ]
        }
    }
    # RULE: a role denied merges carries the text denies of a merge
    if denied:
        out["permissions"] = {"deny": list(MERGE_DENIES)}
    return json.dumps(out)


def _guard_command(role: dict[str, Any], upstream: str) -> str:
    """The guard's command line for a role, with the checks `guard_settings` describes."""
    only_deletes = bool(role.get("settings")) and not role.get("guard")
    denied = denies_merges(role)
    # RULE: the push guard runs isolated from the sessions folder
    command = f"{shlex.quote(sys.executable)} -I -m claude_voice.guard --upstream {shlex.quote(upstream)}"
    # RULE: own-clone mode is on only when the role entry names it exactly
    if role.get("push_mode") == "own-clone" and not only_deletes:
        command += " --push-mode own-clone"
    # RULE: a role with its own settings and no guard gets the delete check
    if only_deletes:
        command += " --push-mode no-deletes"
    # RULE: a role denied merges gets the guard, with only its merge check when it has neither guard nor settings
    if not role.get("guard") and not only_deletes:
        command += " --push-mode merges-only"
    if not denied:
        command += " --merges"
    return command


def settings_for(role: dict[str, Any], name: str = "") -> str | None:
    """A role session's whole settings: the role's own file, if it names one, with the guard laid
    over it. The merge only adds: every hook and deny of either side is kept."""
    guard = guard_settings(role)
    if not role.get("settings"):
        return guard
    path, own, digest = _read_settings(str(role["settings"]), str(role.get("settings_sha256") or ""))
    # RULE: each launch logs the settings files fingerprint
    logger.info("role %s starts with settings %s (sha256 %s)", name, path, digest)
    extra = json.loads(guard) if guard else {}
    hooks = {**own.get("hooks", {})}
    for event, entries in extra.get("hooks", {}).items():
        # RULE: the guard hook is kept beside the files own hooks
        hooks[event] = [*hooks.get(event, []), *entries]
    merged = {**own, **({"hooks": hooks} if hooks else {})}
    denies = extra.get("permissions", {}).get("deny", [])
    if denies:
        permissions = {**own.get("permissions", {})}
        # RULE: the merge denies are kept beside the files own denies
        permissions["deny"] = [*permissions.get("deny", []), *denies]
        merged["permissions"] = permissions
    return json.dumps(merged)


def _read_settings(given: str, pin: str) -> tuple[Path, dict[str, Any], str]:
    """The role's settings file, what it says and its sha256, or LookupError naming the fix.
    The bytes that are hashed against the owner's pin are the bytes that are parsed."""
    folder = SETTINGS_DIR.expanduser().resolve()
    path = Path(given).expanduser()
    if not path.is_file():
        raise LookupError(f"The role's settings file {path} is missing: put it under {folder}, or remove `settings`.")
    real = path.resolve()
    # RULE: a settings file outside the roles folder is refused
    if not real.is_relative_to(folder):
        raise LookupError(f"The role's settings file {path} is outside {folder}: move the file under {folder}.")
    data = real.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    # RULE: a settings file that differs from the owners pin is refused
    if digest != pin.strip().lower():
        raise LookupError(
            f"The role's settings file {path} does not match the settings_sha256 in the role's entry. "
            f"Read the file, then set settings_sha256 to the output of `sha256sum {path}` in {USER_FILE}."
        )
    try:
        own = json.loads(data.decode("utf-8"))
    except ValueError as exc:
        raise LookupError(f"The role's settings file {path} is not JSON ({exc}).") from None
    _check_kinds(own, path)
    _refuse_skips(own, path)
    return real, own, digest


def _check_kinds(own: Any, path: Path) -> None:
    """LookupError naming the fix when a value the merge reads is of the wrong kind."""
    if not isinstance(own, dict):
        raise LookupError(f"The role's settings file {path} must be a JSON object.")
    permissions, hooks = own.get("permissions", {}), own.get("hooks", {})
    if not isinstance(permissions, dict) or not isinstance(hooks, dict):
        raise LookupError(f"In the role's settings file {path}, permissions and hooks must be objects.")
    if not all(isinstance(v, list) for v in [*hooks.values(), *(permissions.get(k, []) for k in RULE_LISTS)]):
        raise LookupError(f"In the role's settings file {path}, each hook event and rule list must be a list.")


def _refuse_skips(own: dict[str, Any], path: Path) -> None:
    # RULE: a settings file that skips checks is refused
    modes = [own.get("defaultMode"), own.get("permissions", {}).get("defaultMode")]
    if any(mode in SKIPS for mode in modes):
        raise LookupError(f"The role's settings file {path} skips the permission checks: remove defaultMode from it.")
    for key in ("disableAllHooks", "skipDangerousModePermissionPrompt"):
        if own.get(key):
            raise LookupError(f"The role's settings file {path} turns checks off: remove {key} from it.")


def public(name: str, role: dict[str, Any]) -> dict[str, Any]:
    """What a client may see of a role."""
    return {"name": name, "title": role.get("title") or name, "description": role.get("description") or ""}
