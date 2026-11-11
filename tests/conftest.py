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
def no_real_github_for_the_software_report(monkeypatch):
    """A bridge started in a test builds its software report behind; it finds no `gh` to ask,
    so no test reaches GitHub. A test that wants answers passes its own fake runner."""
    import types

    from claude_voice import procs, software

    finder = types.SimpleNamespace(find=lambda name: None if name == "gh" else procs.find(name))
    monkeypatch.setattr(software, "procs", finder)
