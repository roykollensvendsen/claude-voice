"""The settings come from the plugin's own config folder, and a key it does not know is refused."""

import pathlib

import pytest

from gizmo_mcp import settings
from gizmo_mcp.platform.linux import Linux

ROOT = pathlib.Path(__file__).parent.parent


def test_no_config_file_gives_the_defaults(tmp_path):
    assert settings.load(tmp_path / "missing.toml") == settings.Settings()


def test_the_shipped_default_config_loads_as_the_defaults():
    """Install writes config.default.toml as the first config, so it must read back cleanly."""
    assert settings.load(ROOT / "config.default.toml") == settings.Settings()


def test_a_key_the_plugin_does_not_know_is_refused_by_name(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[device]\naddres = "x"\n[other]\n', encoding="utf-8")
    with pytest.raises(settings.SettingsError, match=r"device\.addres, other|other, device\.addres"):
        settings.load(path)


def test_a_config_that_is_not_toml_says_how_to_fix_it(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("[device\n", encoding="utf-8")
    with pytest.raises(settings.SettingsError, match="delete it to use the defaults"):
        settings.load(path)


def test_the_config_is_found_in_the_plugin_s_own_folder(monkeypatch, tmp_path):
    monkeypatch.delenv(settings.CONFIG_ENV)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert settings.config_file() == tmp_path / "gizmo-mcp" / "config.toml"
    monkeypatch.setenv(settings.CONFIG_ENV, str(tmp_path / "other.toml"))
    assert settings.config_file() == tmp_path / "other.toml"


def test_the_config_home_falls_back_to_the_home_folder(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", "")
    monkeypatch.setenv("HOME", str(tmp_path))
    assert Linux().config_home() == tmp_path / ".config"
