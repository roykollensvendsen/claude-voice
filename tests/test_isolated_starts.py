"""Every Python the bridge starts with its own module runs isolated from the folder it starts in.

Claude Code runs the bridge's hooks in the session's own folder, and systemd starts the agent
and the updater in the owner's home folder. Each start's own flags are tried here, and the
module must still import.
"""

from __future__ import annotations

import json
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from claude_voice import agent, cli, phone_hook, roles, server

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="runs a shell command line")


def imports_from_its_folder(argv: list[str], folder: Path) -> bool:
    """Whether `argv`, run in `folder`, would take claude_voice from that folder."""
    planted = folder / "claude_voice"
    planted.mkdir()
    (planted / "__init__.py").write_text("raise SystemExit('planted')\n")
    flags = argv[: argv.index("-m")]
    script = f"import {argv[argv.index('-m') + 1]}"  # the real module still imports with these flags
    out = subprocess.run([*flags, "-c", script], cwd=folder, capture_output=True, text=True, timeout=30, check=False)
    if "planted" in out.stderr:
        return True
    assert out.returncode == 0, out.stderr
    return False


@posix_only
def test_the_phone_hook_runs_isolated_from_the_sessions_folder(tmp_path):
    entries = phone_hook.settings_entries(shlex.quote(sys.executable))
    for n, event in enumerate(entries.values()):  # the question hook and the permission hook
        folder = tmp_path / str(n)
        folder.mkdir()
        assert not imports_from_its_folder(shlex.split(event[0]["hooks"][0]["command"]), folder)


@posix_only
def test_the_push_guard_runs_isolated_from_the_sessions_folder(tmp_path):
    settings = json.loads(roles.guard_settings({"guard": True, "upstream": "owner/name"}) or "{}")
    command = settings["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
    assert not imports_from_its_folder(shlex.split(command), tmp_path)


@posix_only
def test_the_agent_runs_isolated_from_the_folder_systemd_starts_it_in(tmp_path, monkeypatch):
    started: list[list[str]] = []
    monkeypatch.setattr(agent, "install", lambda home, command, *a, **k: started.append(command) or 0)
    cli.agent_command("install", {"HOME": str(tmp_path)})
    assert not imports_from_its_folder(started[0], tmp_path)


@posix_only
def test_the_updater_runs_isolated_from_the_folder_systemd_starts_it_in(tmp_path):
    argv = server.updater_command(sys.executable, tmp_path / "clone", tmp_path / "log", 8811)
    assert not imports_from_its_folder(argv, tmp_path)
