"""The manifest a plugin ships at its checkout's root, so the bridge can install it.

``claude-voice-plugin.toml`` is data only: read with tomllib, never run. Its format is
published as ``docs/plugin-manifest/v1.json`` for each plugin's own CI, and checked here in
code, so the bridge needs no schema library; a test puts the same cases to both.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import PLUGIN_NAME
from .fields import TYPES, Field

FILE = "claude-voice-plugin.toml"
VERSION = 1
SHARED = ("home",)  # the shared values a plugin may use
DIR = re.compile(r"[a-z][a-z0-9-]{0,40}")
# Folders under ~/.config that the system or the bridge uses: a plugin's config never goes there.
# No list like this is complete, so install also asks before writing to a folder no known plugin has.
RESERVED = frozenset(
    {
        "claude-voice",
        "claude-voice-home",
        "claude",
        "systemd",
        "autostart",
        "environment.d",
        "git",
        "gh",
        "ssh",
        "gnupg",
        "dconf",
        "pulse",
        "pipewire",
        "fontconfig",
        "containers",
        "docker",
        "uv",
        "pip",
        "nvim",
        "fish",
        "kitty",
        "mimeapps",
        "user-dirs",
    }
)
KEY = re.compile(r"[a-z_][a-z0-9_]*(\.[a-z_][a-z0-9_]*)*")
SECRET = re.compile(r"password|passwd|token|secret|api_?key|credential", re.IGNORECASE)
TOOL = re.compile(r"[A-Za-z0-9_-]{1,64}")
RELATIVE = re.compile(r"[A-Za-z0-9._-]+(/[A-Za-z0-9._-]+)*")
TOP = {"manifest", "name", "command", "args", "tools", "timeout", "branch", "uses", "secrets_step", "config"}
CONFIG = {"default", "dir", "personal"}
PERSONAL = {"key", "type", "ask", "range", "round", "max_len", "values"}
# Which of a personal field's keys each type takes.
EXTRA = {"int": {"range"}, "float": {"range", "round"}, "str": {"max_len"}, "choice": {"values"}}


class ManifestError(ValueError):
    """A manifest that cannot be used; the message says what is wrong in it."""


@dataclass(frozen=True)
class Personal:
    """One value only the owner can give: where it goes in the plugin's config, the question,
    and what fits."""

    key: str
    ask: str
    field: Field


@dataclass(frozen=True)
class PluginConfig:
    default: str  # the default config, relative to the checkout
    dir: str  # the folder under ~/.config
    personal: tuple[Personal, ...]


@dataclass(frozen=True)
class Manifest:
    name: str
    command: str
    args: tuple[str, ...]
    tools: tuple[str, ...]
    timeout: float
    branch: str
    uses: tuple[str, ...]
    secrets_step: str | None
    config: PluginConfig | None


def read(folder: Path) -> Manifest:
    """The manifest in a checkout, or ManifestError saying what is wrong."""
    path = folder / FILE
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ManifestError(
            f"{path} does not exist; a plugin ships one at its root (docs/plugin-manifest.md)"
        ) from None
    except (OSError, UnicodeDecodeError) as exc:
        raise ManifestError(f"{path} could not be read ({exc})") from None
    except tomllib.TOMLDecodeError as exc:
        raise ManifestError(f"{path} is not valid TOML ({exc})") from None
    return parse(raw)


def _known(table: dict[str, Any], allowed: set[str], where: str) -> None:
    unknown = sorted(set(table) - allowed)
    # RULE: a manifest key the format does not know is refused
    # schema: /additionalProperties, /properties/config/additionalProperties, /$defs/personal/additionalProperties
    if unknown:
        raise ManifestError(f"{where} has a key the format does not know: {unknown[0]} (docs/plugin-manifest.md)")


def _texts(value: Any, key: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ManifestError(f"{key} must be a list of text")
    return tuple(value)


def _text(value: Any, key: str) -> str:
    if not isinstance(value, str) or not value:
        raise ManifestError(f"{key} must be text that is not empty")
    return value


def _number(value: Any, key: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ManifestError(f"{key} must be a number")
    return value


def parse(raw: dict[str, Any]) -> Manifest:
    """The manifest as the bridge uses it, or ManifestError naming the first thing wrong."""
    if not isinstance(raw, dict):
        raise ManifestError("the manifest must be a table of keys")
    _known(raw, TOP, "the manifest")
    # RULE: a manifest of another format version is refused
    # schema: /properties/manifest/const
    if raw.get("manifest") != VERSION:
        raise ManifestError(f"manifest = {raw.get('manifest')!r}: this bridge reads format version {VERSION}")
    name = raw.get("name")
    # RULE: a manifest name outside the plugin name pattern is refused
    # schema: /properties/name/pattern
    if not isinstance(name, str) or not PLUGIN_NAME.fullmatch(name):
        raise ManifestError(f"name = {name!r} must be lowercase letters, digits and _, starting with a letter")
    uses = _uses(raw.get("uses", []))
    timeout = _number(raw.get("timeout", 30), "timeout")
    if timeout <= 0:
        raise ManifestError("timeout must be more than 0 seconds")
    steps = raw.get("secrets_step")
    return Manifest(
        name=name,
        command=_text(raw.get("command"), "command"),
        args=_texts(raw.get("args", []), "args"),
        tools=_tools(raw.get("tools", [])),
        timeout=timeout,
        branch=_text(raw.get("branch", "main"), "branch"),
        uses=uses,
        secrets_step=None if steps is None else _text(steps, "secrets_step"),
        config=_config(raw.get("config"), uses),
    )


def _tools(value: Any) -> tuple[str, ...]:
    tools = _texts(value, "tools")
    # RULE: a manifest without a tool is refused
    # schema: /properties/tools/minItems
    if not tools:
        raise ManifestError("tools must name at least one tool the voice may call")
    bad = [t for t in tools if not TOOL.fullmatch(t)]
    if bad or len(set(tools)) != len(tools):
        raise ManifestError(f"tools: {bad[0] if bad else 'a tool named twice'} is not a tool name")
    return tools


def _uses(value: Any) -> tuple[str, ...]:
    uses = _texts(value, "uses")
    unknown = [u for u in uses if u not in SHARED]
    # RULE: a shared value the format does not know is refused
    # schema: /properties/uses/items/enum
    if unknown:
        raise ManifestError(f"uses: {unknown[0]} is not a shared value; there is only {', '.join(SHARED)}")
    if len(set(uses)) != len(uses):
        raise ManifestError("uses names a shared value twice")
    return uses


def _config(raw: Any, uses: tuple[str, ...]) -> PluginConfig | None:
    if raw is None:
        if "home" in uses:
            raise ManifestError('uses = ["home"] needs a [config]: home_file is written into the plugin\'s config')
        return None
    if not isinstance(raw, dict):
        raise ManifestError("config must be a table")
    _known(raw, CONFIG, "[config]")
    folder = raw.get("dir")
    # RULE: a config folder outside the pattern or used by the system is refused
    # schema: /properties/config/properties/dir/pattern, /properties/config/properties/dir/not
    if not isinstance(folder, str) or not DIR.fullmatch(folder) or folder in RESERVED:
        raise ManifestError(f"[config] dir = {folder!r} must be one plain folder name no system tool uses")
    default = _text(raw.get("default"), "[config] default")
    if not RELATIVE.fullmatch(default) or any(part in (".", "..") for part in default.split("/")):
        raise ManifestError(f"[config] default = {default!r} must be a path inside the checkout")
    personal = raw.get("personal", [])
    if not isinstance(personal, list):
        raise ManifestError("[[config.personal]] must be a list of tables")
    return PluginConfig(default, folder, tuple(_personal(p, n) for n, p in enumerate(personal, 1)))


def _personal(raw: Any, number: int) -> Personal:
    where = f"[[config.personal]] number {number}"
    if not isinstance(raw, dict):
        raise ManifestError(f"{where} must be a table")
    _known(raw, PERSONAL, where)
    key = raw.get("key")
    if not isinstance(key, str) or not KEY.fullmatch(key) or key == "home_file":
        raise ManifestError(f"{where}: key = {key!r} must be a dotted lowercase key, and not home_file")
    # RULE: a personal field that names a secret is refused
    # schema: /$defs/personal/properties/key/not
    if SECRET.search(key):
        raise ManifestError(f"{where}: {key} looks like a secret; a secret goes through the plugin's own secrets_step")
    kind = raw.get("type")
    if kind not in TYPES:
        raise ManifestError(f"{where}: type = {kind!r} must be one of {', '.join(TYPES)}")
    wrong = sorted(set(raw) - {"key", "type", "ask"} - EXTRA[kind])
    if wrong:
        raise ManifestError(f"{where}: {wrong[0]} is not for a {kind}")
    return Personal(key, _text(raw.get("ask"), f"{where}: ask"), _field(raw, kind, where))


def _field(raw: dict[str, Any], kind: str, where: str) -> Field:
    if kind == "str":
        longest = raw.get("max_len")
        if isinstance(longest, bool) or not isinstance(longest, int) or not 1 <= longest <= 1000:
            raise ManifestError(f"{where}: a str needs max_len, from 1 to 1000")
        return Field(kind, max_len=longest)
    if kind == "choice":
        values = _texts(raw.get("values"), f"{where}: values")
        if not values or not all(values) or len(set(values)) != len(values):
            raise ManifestError(f"{where}: a choice needs values, each named once")
        return Field(kind, values=values)
    decimals = raw.get("round")
    if decimals is not None and (isinstance(decimals, bool) or not isinstance(decimals, int) or not 0 <= decimals <= 6):
        raise ManifestError(f"{where}: round must be a whole number of decimals from 0 to 6")
    return Field(kind, range=_range(raw.get("range"), where), round=decimals)


def _range(raw: Any, where: str) -> tuple[float, float] | None:
    if raw is None:
        return None
    if not isinstance(raw, list) or len(raw) != 2:
        raise ManifestError(f"{where}: range must be [lowest, highest]")
    low, high = (_number(v, f"{where}: range") for v in raw)
    if low > high:
        raise ManifestError(f"{where}: range must be [lowest, highest]")
    return (low, high)
