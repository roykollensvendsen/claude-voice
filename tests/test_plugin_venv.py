"""A plugin declared as `uv run <script>` starts from its own environment, synced just before.

`uv run` stays alive as the plugin's parent for as long as the plugin runs, and holds uv's
cache lock all that time, so the weekly cache prune could never run. The bridge instead runs
a short `uv sync --locked` in the plugin's folder and starts the script from `.venv` itself.
"""

import json
import os
import stat
import sys
import textwrap
from pathlib import Path

import pytest

from claude_voice import config, plugin_venv, plugins

# The stand-in uv is a script with a #! line, which Windows cannot run; the path test below can.
posix = pytest.mark.skipif(sys.platform == "win32", reason="the stand-in uv needs a #! line and flock")

FAKE_UV = textwrap.dedent(
    """\
    #!{python}
    # A stand-in for uv: notes how it was called, then does what the folder's .uv-does says.
    import json, os, pathlib, sys, time
    here = pathlib.Path.cwd()
    with open(here / ".uv-calls", "a") as f:
        f.write(json.dumps({{"argv": sys.argv[1:], "env": sorted(os.environ)}}) + "\\n")
    does = (here / ".uv-does").read_text().strip() if (here / ".uv-does").exists() else "make"
    if does == "fail":
        print("error: The lockfile at `uv.lock` needs to be updated", file=sys.stderr)
        sys.exit(2)
    if does == "sleep":
        time.sleep(30)
    if does == "make":
        script = here / ".venv" / "bin" / "gizmo-mcp"
        script.parent.mkdir(parents=True, exist_ok=True)
        script.write_text("#!/bin/sh\\n")
        script.chmod(0o755)
    """
)


@pytest.fixture
def uv(tmp_path, monkeypatch):
    """A fake uv the bridge finds, and a lock file of the suite's own."""
    fake = tmp_path / "bin" / "uv"
    fake.parent.mkdir()
    fake.write_text(FAKE_UV.format(python=sys.executable))
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setattr(plugin_venv, "find_uv", lambda: str(fake))
    monkeypatch.setattr(plugin_venv, "LOCK", tmp_path / "state" / "uv.lock")
    return fake


def gizmo(folder: Path, *args: str, command: str = "uv") -> config.PluginSpec:
    folder.mkdir(exist_ok=True)
    return config.PluginSpec("gizmo", command, list(args), str(folder), ["gizmo_status"], 2.0)


def calls(folder: Path) -> list[dict]:
    log = folder / ".uv-calls"
    return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []


@posix
def test_a_plugin_declared_as_uv_run_starts_from_its_own_environment(tmp_path, uv):
    spec = gizmo(tmp_path / "gizmo", "run", "gizmo-mcp", "serve")
    started = plugins.parameters(spec)
    assert started.command == str(tmp_path / "gizmo" / ".venv" / "bin" / "gizmo-mcp")
    assert started.args == ["serve"] and started.cwd == spec.cwd
    assert [c["argv"] for c in calls(tmp_path / "gizmo")] == [["sync", "--locked"]]


@posix
def test_a_plugin_with_no_environment_yet_gets_one_from_the_sync(tmp_path, uv):
    folder = tmp_path / "gizmo"
    spec = gizmo(folder, "run", "gizmo-mcp", "serve")
    assert not (folder / ".venv").exists()
    assert plugins.parameters(spec).command == str(folder / ".venv" / "bin" / "gizmo-mcp")


@posix
@pytest.mark.parametrize(
    ("command", "args"),
    [
        ("uv", ["run", "../gizmo-mcp"]),
        ("uv", ["run", "tools/gizmo-mcp"]),
        ("uv", ["run", "..", "serve"]),
        ("uv", ["run", "--with", "gizmo", "gizmo-mcp"]),
        ("uv", ["run"]),
        ("uv", ["tool", "run", "gizmo-mcp"]),
        ("uvx", ["gizmo-mcp"]),
        ("gizmo-mcp", ["run", "gizmo-mcp"]),
    ],
)
def test_only_uv_run_with_a_plain_script_name_is_started_from_the_plugins_own_environment(tmp_path, uv, command, args):
    spec = gizmo(tmp_path / "gizmo", *args, command=command)
    started = plugins.parameters(spec)
    assert Path(started.command).name == command and started.args == args
    assert calls(tmp_path / "gizmo") == []  # nothing else is synced either


@posix
def test_a_plugin_starts_only_from_inside_its_own_environment(tmp_path, uv):
    folder = tmp_path / "gizmo"
    elsewhere = tmp_path / "elsewhere"
    elsewhere.write_text("#!/bin/sh\n")
    (folder / ".uv-does").parent.mkdir()
    (folder / ".uv-does").write_text("nothing")
    (folder / ".venv" / "bin").mkdir(parents=True)
    (folder / ".venv" / "bin" / "gizmo-mcp").symlink_to(elsewhere)
    started = plugins.parameters(gizmo(folder, "run", "gizmo-mcp"))
    assert Path(started.command).name == "uv" and started.args == ["run", "gizmo-mcp"]


@posix
def test_a_plugin_whose_sync_fails_is_refused_with_the_command_to_fix_it(tmp_path, uv):
    folder = tmp_path / "gizmo"
    spec = gizmo(folder, "run", "gizmo-mcp")
    (folder / ".uv-does").write_text("fail")
    with pytest.raises(plugin_venv.Refused) as refused:
        plugins.parameters(spec)
    said = str(refused.value)
    assert "failed" in said and "needs to be updated" in said
    assert str(folder) in said and "uv sync --locked" in said


@posix
def test_a_plugin_without_its_entry_point_after_a_sync_is_refused(tmp_path, uv):
    folder = tmp_path / "gizmo"
    spec = gizmo(folder, "run", "gizmo-mcp")
    (folder / ".uv-does").write_text("nothing")
    with pytest.raises(plugin_venv.Refused) as refused:
        plugins.parameters(spec)
    assert "gizmo-mcp" in str(refused.value) and str(folder) in str(refused.value)
    assert "uv sync --locked" in str(refused.value)


@posix
def test_a_sync_that_runs_too_long_is_stopped_and_refused(tmp_path, uv, monkeypatch):
    monkeypatch.setattr(plugin_venv, "SYNC_SECONDS", 0.5)
    folder = tmp_path / "gizmo"
    spec = gizmo(folder, "run", "gizmo-mcp")
    (folder / ".uv-does").write_text("sleep")
    with pytest.raises(plugin_venv.Refused) as refused:
        plugins.parameters(spec)
    assert "0.5 s" in str(refused.value) and "uv sync --locked" in str(refused.value)


@posix
def test_a_sync_waits_its_turn_behind_other_uv_work(tmp_path, uv, monkeypatch):
    import fcntl

    monkeypatch.setattr(plugin_venv, "SYNC_SECONDS", 0.5)
    folder = tmp_path / "gizmo"
    spec = gizmo(folder, "run", "gizmo-mcp")
    plugin_venv.LOCK.parent.mkdir(parents=True)
    other = os.open(plugin_venv.LOCK, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(other, fcntl.LOCK_EX)  # a prune, or the updater's install, holds it
        with pytest.raises(plugin_venv.Refused) as refused:
            plugins.parameters(spec)
    finally:
        os.close(other)
    assert calls(folder) == [] and "other uv work" in str(refused.value)
    assert plugins.parameters(spec).command.endswith("gizmo-mcp")  # and goes once it is free


@posix
def test_a_sync_gets_none_of_the_bridges_secrets(tmp_path, uv, monkeypatch):
    monkeypatch.setenv("CLAUDE_VOICE_TOKEN", "a secret")
    folder = tmp_path / "gizmo"
    plugins.parameters(gizmo(folder, "run", "gizmo-mcp"))
    seen = calls(folder)[0]["env"]
    assert "CLAUDE_VOICE_TOKEN" not in seen and "HOME" in seen


@posix
def test_an_unapproved_plugin_is_never_synced(tmp_path, uv):
    folder = tmp_path / "gizmo"
    spec = gizmo(folder, "run", "gizmo-mcp")
    (folder / ".venv" / "bin").mkdir(parents=True)  # an environment from before is no approval
    plugin, _, why = plugins.check_one(spec, tmp_path / "approvals.json", client=lambda p: pytest.fail("started"))
    assert plugin is None and why == "not approved" and calls(folder) == []


@posix
def test_a_started_plugin_is_synced_and_runs_from_its_environment(tmp_path, uv):
    folder = tmp_path / "gizmo"
    spec = gizmo(folder, "run", "gizmo-mcp", "serve")
    seen = []

    class Records:
        def __init__(self, params):
            seen.append(params)

        async def __aenter__(self):
            raise ConnectionError("only how it was started matters here")

        async def __aexit__(self, *exc):
            return None

    plugins.Plugin(spec, Records).listing()  # a start and a restart both list it first
    assert [p.command for p in seen] == [str(folder / ".venv" / "bin" / "gizmo-mcp")]
    assert calls(folder)[0]["argv"] == ["sync", "--locked"]


@posix
def test_a_refused_plugin_says_why_when_it_is_listed(tmp_path, uv, caplog):
    folder = tmp_path / "gizmo"
    spec = gizmo(folder, "run", "gizmo-mcp")
    (folder / ".uv-does").write_text("fail")
    plugin = plugins.Plugin(spec, lambda p: pytest.fail("started"))
    assert plugin.listing() == {}
    assert "uv sync --locked" in (plugin.unlisted or "") and str(folder) in (plugin.unlisted or "")


def test_on_windows_the_entry_point_is_in_scripts_with_exe():
    folder = Path("plugins") / "gizmo"
    assert plugin_venv.entry_point(folder, "gizmo-mcp", windows=True) == folder / ".venv" / "Scripts" / "gizmo-mcp.exe"
    assert plugin_venv.entry_point(folder, "gizmo-mcp", windows=False) == folder / ".venv" / "bin" / "gizmo-mcp"


def test_the_manifest_page_gives_the_syncs_time_limit():
    page = (Path(__file__).parent.parent / "docs" / "plugin-manifest.md").read_text(encoding="utf-8")
    assert plugin_venv.SYNC_SECONDS == 300 and "waits up to five minutes" in page
