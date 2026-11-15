"""The plugin's own settings, read from its own config folder.

`claude-voice plugins install` writes `config.default.toml` there the first time, so the file
holds exactly the keys that file has. A key it does not know is refused by name, so a typo
never passes as a default silently.
"""

from __future__ import annotations

import os
import pathlib
import tomllib
from dataclasses import dataclass

from gizmo_mcp import platform

#: The folder under the config home, as the manifest's `config.dir` names it.
CONFIG_DIR = "gizmo-mcp"
#: Points at another config file, for a test or a second gizmo.
CONFIG_ENV = "GIZMO_MCP_CONFIG"


class SettingsError(ValueError):
    """The config file cannot be used; the message names the fix."""


@dataclass(frozen=True)
class Settings:
    """What the plugin reads from its config file."""

    address: str = "gizmo.example.invalid"
    simulate: bool = True


def config_file() -> pathlib.Path:
    """Where the config is: `$GIZMO_MCP_CONFIG`, or the plugin's folder in the config home."""
    named = os.environ.get(CONFIG_ENV)
    return pathlib.Path(named) if named else platform.current().config_home() / CONFIG_DIR / "config.toml"


def load(path: pathlib.Path | None = None) -> Settings:
    """The settings in `path`, or the defaults when there is no file."""
    path = config_file() if path is None else path
    if not path.is_file():
        return Settings()
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as broken:
        msg = f"{path} is not valid TOML ({broken}); fix it, or delete it to use the defaults"
        raise SettingsError(msg) from broken
    device = data.pop("device", {})
    unknown = sorted(data) + sorted(f"device.{key}" for key in device if key not in ("address", "simulate"))
    if unknown:
        msg = f"{path} has keys gizmo-mcp does not know: {', '.join(unknown)}; remove them or fix their spelling"
        raise SettingsError(msg)
    return Settings(address=str(device.get("address", Settings.address)), simulate=bool(device.get("simulate", True)))
