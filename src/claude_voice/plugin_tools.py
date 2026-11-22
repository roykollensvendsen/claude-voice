"""`plugins tools <name> --from-manifest`: set a plugin's tools from its own manifest
(plugin-config-commands-v1, 2.1 and b2).

Nothing typed goes into the list: it comes from the manifest in the plugin's checkout, which
must be clean and level with the branch it follows. Only tools new to the manifest since the
owner's last approval are added, so a tool the owner left out stays out, and tools gone from
the manifest are removed. It never approves: a new tool waits for the owner.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from . import gitstatus, manifest, plugins, safefile
from .config import plugin_spec
from .settings_edit import Refused, _load

#: Where the settings file is copied before each change, under the owner's home.
BACKUPS = Path(".local/state/claude-voice/backups/bridge-config")


def _manifest(name: str, table: dict[str, Any], cwd: Path) -> manifest.Manifest:
    """The plugin's manifest, when it is this plugin's and runs as the settings say."""
    try:
        found = manifest.read(cwd)
    except manifest.ManifestError as exc:
        raise Refused(str(exc)) from None
    if found.name != name:
        raise Refused(f"the manifest in {cwd} is {found.name}'s, not {name}'s")
    # RULE: a manifest of another plugin or command is refused
    if found.command != table.get("command") or list(found.args) != list(table.get("args") or []):
        raise Refused(f"{name}'s manifest runs another command than the settings; that is a move, the owner's to make")
    return found


def wanted(configured: list[str], before: list[str], now: tuple[str, ...]) -> list[str]:
    """The tools after the change: the configured ones still in the manifest, then those new to
    it since the last approval."""
    # RULE: a tool the owner left out is not added again
    return [t for t in configured if t in now] + [t for t in now if t not in before and t not in configured]


def from_manifest(
    config: Path,
    approvals: Path | None,
    name: str,
    *,
    home: Path,
    git: Callable[[str], dict[str, Any] | None] = gitstatus.read,
    now: datetime | None = None,
) -> list[str]:
    """Set the plugin's tools from its manifest: lines saying what changed and how to undo it,
    or Refused saying why not."""
    raw = _load(config)
    table = raw.get("plugins", {}).get(name)
    if not isinstance(table, dict):
        raise Refused(f"no plugin {name!r} in {config}; add one with claude-voice plugins install")
    spec = plugin_spec(name, table, str(home))
    # RULE: tools come only from a clean checkout level with its branch
    why = plugins.checkout_refusal(git(spec.cwd), spec.branch)
    if why:
        raise Refused(f"{name}'s tools are read only from reviewed code, and {why}")
    found = _manifest(name, table, Path(spec.cwd))
    before = (plugins.read_approvals(approvals).get(name) or {}).get("manifest_tools")
    # RULE: tools are never set before a first approval stored the manifest
    if not isinstance(before, list):
        raise Refused(f"approve {name} once first, so the tools left out on purpose are known")
    after = wanted(list(spec.tools), before, found.tools)
    if after == list(spec.tools):
        return [f"{name}: the tools already match its manifest; nothing changed"]
    if not after:
        raise Refused(f"{name}'s manifest has none of its tools left; that is the owner's to settle")
    kept = _backup(config, home / BACKUPS, now or datetime.now())
    table["tools"] = after
    safefile.replace(config, _dumped(raw))
    return [
        f"{name}: tools = {list(spec.tools)} -> {after}",
        "New tools wait for the owner's approval; nothing was approved.",
        f"To undo, copy {kept} back over {config}",
    ]


def _backup(config: Path, folder: Path, now: datetime) -> Path:
    safefile.private_folder(folder)
    stamp = now.strftime("%Y%m%d-%H%M%S")
    spare = next(
        p for n in range(1000) if not (p := folder / f"{config.name}.{stamp}{f'-{n}' if n else ''}.bak").exists()
    )
    safefile.replace(spare, config.read_bytes())
    return spare


def _dumped(raw: Any) -> str:
    import tomlkit

    return tomlkit.dumps(raw)  # tomlkit keeps the owner's comments and order
