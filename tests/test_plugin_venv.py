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
import time
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
    if does == "spawn":  # a build step of its own, which outlives uv unless its group is stopped
        import subprocess
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)", str(here)])
        (here / ".uv-child").write_text(str(child.pid))
        time.sleep(30)
    if does == "orphan":  # a helper that leaves uv's group, yet keeps uv's output open
        import subprocess
        child = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)", str(here)], start_new_session=True
        )
        (here / ".uv-child").write_text(str(child.pid))
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


def _runs(pid: int, marker: str) -> bool:
    """Whether `pid` is still the stand-in, by its own marker, since a number can be reused."""
    try:
        return marker in Path(f"/proc/{pid}/cmdline").read_bytes().decode(errors="replace")
    except OSError:
        return False


@posix
def test_a_sync_that_runs_too_long_is_stopped_with_everything_it_started(tmp_path, uv, monkeypatch):
    monkeypatch.setattr(plugin_venv, "SYNC_SECONDS", 2.0)
    folder = tmp_path / "gizmo"
    spec = gizmo(folder, "run", "gizmo-mcp")
    (folder / ".uv-does").write_text("spawn")
    child = None
    try:
        began = time.monotonic()
        with pytest.raises(plugin_venv.Refused):
            plugins.parameters(spec)
        assert time.monotonic() - began < 10  # not held until the build step ends by itself
        child = int((folder / ".uv-child").read_text())
        deadline = time.monotonic() + 5
        while _runs(child, str(folder)) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert not _runs(child, str(folder))
    finally:
        if child is not None and _runs(child, str(folder)):
            os.kill(child, 9)


@posix
def test_a_sync_still_running_when_the_bridge_stops_is_stopped_with_everything_it_started(tmp_path, uv):
    """A sync in a worker thread never sees a Ctrl-C, so the bridge stops it as it stops itself."""
    import threading

    folder = tmp_path / "gizmo"
    gizmo(folder, "run", "gizmo-mcp")
    (folder / ".uv-does").write_text("spawn")
    ended: list[object] = []
    worker = threading.Thread(target=lambda: ended.append(plugin_venv._sync(str(uv), folder, 60)), daemon=True)
    worker.start()
    child = None
    try:
        deadline = time.monotonic() + 5
        while not (folder / ".uv-child").exists() and time.monotonic() < deadline:
            time.sleep(0.05)  # until the sync has started its own helper
        child = int((folder / ".uv-child").read_text())
        assert plugin_venv.stop_running() == 1
        worker.join(5)
        assert ended and not worker.is_alive()  # the sync's thread is let go at once
        deadline = time.monotonic() + 5
        while _runs(child, str(folder)) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert not _runs(child, str(folder))
        assert plugin_venv.stop_running() == 0  # an ended sync is no longer counted
    finally:
        if child is not None and _runs(child, str(folder)):
            os.kill(child, 9)


@posix
def test_a_stopped_sync_never_waits_on_output_a_helper_outside_its_group_holds(tmp_path, uv, monkeypatch, caplog):
    monkeypatch.setattr(plugin_venv, "SYNC_SECONDS", 1.0)
    monkeypatch.setattr(plugin_venv, "DRAIN_SECONDS", 0.5)
    folder = tmp_path / "gizmo"
    spec = gizmo(folder, "run", "gizmo-mcp")
    (folder / ".uv-does").write_text("orphan")
    child = None
    try:
        began = time.monotonic()
        with pytest.raises(plugin_venv.Refused):
            plugins.parameters(spec)
        # the helper sleeps a minute with uv's output open; the sync must not wait for it
        assert time.monotonic() - began < 15  # the time limit, the short last read, and slack
        # the helper keeps running, so the log says one was left
        assert "left a helper that still holds its output" in caplog.text
    finally:
        marker = folder / ".uv-child"
        child = int(marker.read_text()) if marker.exists() else None
        if child is not None and _runs(child, str(folder)):
            os.kill(child, 9)


@posix
def test_a_sync_cut_short_by_ctrl_c_is_stopped_with_everything_it_started(tmp_path, uv, monkeypatch):
    """A Ctrl-C in the terminal reaches the bridge, not a sync in a session of its own."""
    import subprocess

    folder = tmp_path / "gizmo"
    gizmo(folder, "run", "gizmo-mcp")
    (folder / ".uv-does").write_text("spawn")
    started = subprocess.Popen.communicate

    def interrupted(self, *args, **kwargs):
        deadline = time.monotonic() + 5
        while not (folder / ".uv-child").exists() and time.monotonic() < deadline:
            time.sleep(0.05)  # until the sync has started its own helper
        monkeypatch.setattr(subprocess.Popen, "communicate", started)
        raise KeyboardInterrupt

    monkeypatch.setattr(subprocess.Popen, "communicate", interrupted)
    child = None
    try:
        with pytest.raises(KeyboardInterrupt):
            plugin_venv._sync(str(uv), folder, 60)
        child = int((folder / ".uv-child").read_text())
        deadline = time.monotonic() + 5
        while _runs(child, str(folder)) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert not _runs(child, str(folder))
    finally:
        if child is not None and _runs(child, str(folder)):
            os.kill(child, 9)


@posix
def test_waiting_for_the_lock_counts_against_the_syncs_time_limit(tmp_path, uv, monkeypatch):
    import fcntl
    import threading

    monkeypatch.setattr(plugin_venv, "SYNC_SECONDS", 1.0)
    folder = tmp_path / "gizmo"
    spec = gizmo(folder, "run", "gizmo-mcp")
    (folder / ".uv-does").write_text("sleep")
    plugin_venv.LOCK.parent.mkdir(parents=True)
    other = os.open(plugin_venv.LOCK, os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(other, fcntl.LOCK_EX)
    threading.Timer(0.7, os.close, [other]).start()  # another uv step lets go after 0.7 s
    began = time.monotonic()
    with pytest.raises(plugin_venv.Refused):
        plugins.parameters(spec)
    assert time.monotonic() - began < 1.5  # one budget of 1 s, not 0.7 s of waiting and 1 s more


@posix
def test_a_plugin_refused_at_start_says_why_and_what_fixes_it(tmp_path, uv):
    folder = tmp_path / "gizmo"
    spec = gizmo(folder, "run", "gizmo-mcp")
    (folder / ".uv-does").write_text("fail")
    approvals = tmp_path / "approvals.json"
    plugins.approve(spec, {}, approvals)
    plugin, _, why = plugins.check_one(spec, approvals, client=lambda p: pytest.fail("started"))
    assert plugin is None and why.startswith("not started: ")
    assert str(folder) in why and "uv sync --locked" in why
