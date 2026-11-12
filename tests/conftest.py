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


@pytest.fixture(autouse=True)
def no_real_github_for_the_software_report(monkeypatch):
    """A bridge started in a test builds its software report behind; it finds no `gh` to ask,
    so no test reaches GitHub. A test that wants answers passes its own fake runner."""
    import types

    from claude_voice import procs, software

    finder = types.SimpleNamespace(find=lambda name: None if name == "gh" else procs.find(name))
    monkeypatch.setattr(software, "procs", finder)


@pytest.fixture(autouse=True)
def no_owner_env_file(tmp_path_factory, monkeypatch):
    """Keep the owner's own environment file out of the suite: it holds his token, and a
    test never reads it. A test that wants one writes its own."""
    from claude_voice import envfile

    none = tmp_path_factory.mktemp("env") / "none"
    monkeypatch.setattr(envfile, "ENV_FILE", none)
    monkeypatch.setenv("CLAUDE_VOICE_ENV_FILE", str(none))  # for the commands the docs run


@pytest.fixture(autouse=True)
def no_real_claude_sign_in(monkeypatch):
    """The work page asks Claude Code when its sign-in runs out. The suite answers that it
    cannot say, so no test runs the owner's Claude Code or depends on his sign-in date."""
    from claude_voice import work

    monkeypatch.setattr(work, "read_login_expiry", lambda: None)
