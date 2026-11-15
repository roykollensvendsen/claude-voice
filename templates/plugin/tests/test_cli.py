"""The command line says what the voice would hear, and a broken config says how to fix it."""

import pytest

from gizmo_mcp import cli


def test_status_says_what_the_voice_would_hear(capsys):
    assert cli.main(["status", "--simulate"]) == 0
    assert capsys.readouterr().out == "Simulated: The gizmo is on, at level 40.\n"


def test_with_no_real_gizmo_set_up_status_says_so(tmp_path, monkeypatch, capsys):
    config = tmp_path / "config.toml"
    config.write_text("[device]\nsimulate = false\n")
    monkeypatch.setenv("GIZMO_MCP_CONFIG", str(config))
    assert cli.main(["status"]) == 0
    assert capsys.readouterr().out == "No real gizmo is set up yet, so nothing was asked.\n"


def test_a_broken_config_stops_the_command_with_the_fix(tmp_path, monkeypatch, capsys):
    config = tmp_path / "config.toml"
    config.write_text("[device\n")
    monkeypatch.setenv("GIZMO_MCP_CONFIG", str(config))
    assert cli.main(["status"]) == 2
    assert "delete it to use the defaults" in capsys.readouterr().err


def test_every_verb_is_one_the_command_takes():
    for verb in cli.COMMANDS:
        with pytest.raises(SystemExit) as shown:
            cli.main([verb, "--help"])
        assert shown.value.code == 0
