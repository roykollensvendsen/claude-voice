"""How the bridge names itself in a hint, so the command it suggests runs from any shell."""

from pathlib import Path

from claude_voice import cli, invocation, plugin_desk
from claude_voice.config import PluginSpec


def _program(folder: Path) -> Path:
    program = folder / "bin" / "claude-voice"
    program.parent.mkdir(parents=True)
    program.write_text("#!/bin/sh\n")
    return program


def test_the_bare_name_is_used_when_the_search_path_finds_this_program(tmp_path, monkeypatch):
    program = _program(tmp_path)
    monkeypatch.setattr(invocation.shutil, "which", lambda name: str(program))
    assert invocation.command(argv0=str(program), home=tmp_path) == "claude-voice"


def test_a_program_off_the_search_path_is_named_by_its_path_from_home(tmp_path, monkeypatch):
    program = _program(tmp_path / "claude-voice/.venv")
    monkeypatch.setattr(invocation.shutil, "which", lambda name: None)
    assert invocation.command(argv0=str(program), home=tmp_path) == "~/claude-voice/.venv/bin/claude-voice"


def test_another_claude_voice_on_the_search_path_is_not_taken_for_this_one(tmp_path, monkeypatch):
    program = _program(tmp_path / "mine")
    other = _program(tmp_path / "other")
    monkeypatch.setattr(invocation.shutil, "which", lambda name: str(other))
    assert invocation.command(argv0=str(program), home=tmp_path / "elsewhere") == str(program)


def test_a_program_started_through_python_is_found_in_its_environment(tmp_path, monkeypatch):
    program = _program(tmp_path / ".venv")
    monkeypatch.setattr(invocation.shutil, "which", lambda name: None)
    named = invocation.command(argv0="/usr/lib/claude_voice/cli.py", prefix=str(tmp_path / ".venv"), home=tmp_path)
    assert named == "~/.venv/bin/claude-voice" and program.exists()


def test_plugins_list_names_a_command_that_runs_when_the_bridge_is_off_the_search_path(tmp_path, monkeypatch):
    from claude_voice import plugins

    program = _program(tmp_path / "claude-voice/.venv")
    monkeypatch.setattr("sys.argv", [str(program), "plugins", "list"])
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr("shutil.which", lambda name: None)
    monkeypatch.setattr(plugins, "check_one", lambda spec: (None, None, "not approved"))
    spec = PluginSpec(name="kassalapp", command="true", args=[], cwd=str(tmp_path), tools=["prices"])
    approve = "run: ~/claude-voice/.venv/bin/claude-voice plugins approve kassalapp"
    assert approve in cli._plugin_state(spec)
    assert "~/claude-voice/.venv/bin/claude-voice plugins approve kassalapp" in plugin_desk._typed("kassalapp")
