"""What every test shares."""

import pytest


@pytest.fixture(autouse=True)
def no_owner_settings(tmp_path_factory, monkeypatch):
    """Keep the owner's own settings file out of the suite, so a plugin he adds on this
    computer changes no test's answer. A test that wants settings writes its own."""
    monkeypatch.setenv("CLAUDE_VOICE_CONFIG", str(tmp_path_factory.mktemp("settings") / "none.toml"))
