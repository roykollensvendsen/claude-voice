"""What every test shares."""

import pytest


@pytest.fixture(autouse=True)
def no_owner_settings(tmp_path_factory, monkeypatch):
    """Keep the owner's own settings file out of the suite, so a plugin he adds on this
    computer changes no test's answer. A test that wants settings writes its own."""
    monkeypatch.setenv("CLAUDE_VOICE_CONFIG", str(tmp_path_factory.mktemp("settings") / "none.toml"))


@pytest.fixture(autouse=True)
def no_owner_developer_mode(tmp_path_factory, monkeypatch):
    """Keep the owner's own developer mode out of the suite: a test never reads it or ends it."""
    from claude_voice import devmode

    monkeypatch.setattr(devmode, "STATE", tmp_path_factory.mktemp("state") / "developer-mode.json")


@pytest.fixture(autouse=True)
def no_owner_env_file(tmp_path_factory, monkeypatch):
    """Keep the owner's own environment file out of the suite: it holds his token, and a
    test never reads it. A test that wants one writes its own."""
    from claude_voice import envfile

    none = tmp_path_factory.mktemp("env") / "none"
    monkeypatch.setattr(envfile, "ENV_FILE", none)
    monkeypatch.setenv("CLAUDE_VOICE_ENV_FILE", str(none))  # for the commands the docs run
