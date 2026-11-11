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
def no_real_side_reader(monkeypatch):
    """A watched server warms the shared side-question reader. A real one starts Claude Code,
    which slows the suite and holds its folder open after it, so the suite gets a fake."""
    from fakes import FakeClaude

    from claude_voice import transcripts

    reader = transcripts.SideReader(client_factory=FakeClaude())
    monkeypatch.setattr(transcripts, "_reader", reader)
    yield
    if reader._cwd is not None:
        reader._cwd.cleanup()


@pytest.fixture(autouse=True)
def no_github(monkeypatch):
    """A watched server asks GitHub for the open pull requests, and waits for the answer before
    its next look at the sessions. The suite answers as if GitHub's command line were missing."""
    from claude_voice import software

    monkeypatch.setattr(software, "_gh", lambda args: None)
