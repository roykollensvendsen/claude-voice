"""Updating claude-voice: pull, install, test, restart, and roll back if anything fails."""

import json
import subprocess

import pytest

from claude_voice import updater


def git(folder, *args):
    return subprocess.run(["git", "-C", str(folder), *args], check=True, capture_output=True, text=True).stdout.strip()


def commit(folder, name):
    (folder / name).write_text(name)
    git(folder, "add", name)
    git(folder, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", f"Add {name}")
    return git(folder, "rev-parse", "HEAD")


@pytest.fixture
def install(tmp_path):
    """The project's repository, and the owner's clone of it that the bridge runs from."""
    origin = tmp_path / "origin"
    origin.mkdir()
    git(origin, "init", "-q", "-b", "main")
    commit(origin, "a")
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", str(origin), str(clone)], check=True)
    return origin, clone


class Service:
    """Stands in for the bridge's systemd service and its health check."""

    def __init__(self, healthy=True, healthy_after_rollback=True):
        self.healthy = healthy
        self.healthy_after_rollback = healthy_after_rollback
        self.restarts = 0
        self.said = []  # each note and each restart, in the order they happened

    def restart(self):
        self.restarts += 1
        self.said.append("restart")

    def note(self, reason, frm, to):
        self.said.append((reason, frm, to))

    def health(self):
        return self.healthy if self.restarts == 1 else self.healthy_after_rollback


def run(clone, tmp_path, service, tests_pass=True):
    log = tmp_path / "update.json"
    outcome = updater.run(
        clone,
        log,
        install=lambda folder: (True, "installed"),
        tests=lambda folder: (tests_pass, "12 passed" if tests_pass else "1 failed"),
        restart=service.restart,
        healthy=service.health,
        note=service.note,
    )
    steps = [s["step"] for s in json.loads(log.read_text())["steps"]]
    return outcome, steps


def test_an_install_already_up_to_date_is_left_alone(install, tmp_path):
    _, clone = install
    service = Service()
    outcome, steps = run(clone, tmp_path, service)
    assert outcome["result"] == "up_to_date" and service.restarts == 0
    assert steps == ["check", "fetch", "done"]


def test_a_newer_version_is_pulled_tested_and_started(install, tmp_path):
    origin, clone = install
    new = commit(origin, "b")
    service = Service()
    outcome, steps = run(clone, tmp_path, service)
    assert outcome["result"] == "updated" and git(clone, "rev-parse", "HEAD") == new
    assert steps == ["check", "fetch", "pull", "install", "test", "restart", "verify", "done"]
    assert service.restarts == 1


def test_local_changes_stop_the_update_before_anything_is_touched(install, tmp_path):
    origin, clone = install
    commit(origin, "b")
    (clone / "a").write_text("my own change")
    before = git(clone, "rev-parse", "HEAD")
    service = Service()
    outcome, steps = run(clone, tmp_path, service)
    assert outcome["result"] == "stopped" and "local changes" in outcome["detail"]
    assert git(clone, "rev-parse", "HEAD") == before and (clone / "a").read_text() == "my own change"
    assert service.restarts == 0


def test_an_update_whose_tests_fail_is_rolled_back_without_a_restart(install, tmp_path):
    origin, clone = install
    before = git(clone, "rev-parse", "HEAD")
    commit(origin, "b")
    service = Service()
    outcome, steps = run(clone, tmp_path, service, tests_pass=False)
    assert outcome["result"] == "rolled_back" and git(clone, "rev-parse", "HEAD") == before
    assert service.restarts == 0 and "rollback" in steps


def test_an_update_that_does_not_come_back_healthy_is_rolled_back_and_restarted(install, tmp_path):
    origin, clone = install
    before = git(clone, "rev-parse", "HEAD")
    commit(origin, "b")
    service = Service(healthy=False)
    outcome, steps = run(clone, tmp_path, service)
    assert outcome["result"] == "rolled_back" and git(clone, "rev-parse", "HEAD") == before
    assert service.restarts == 2  # once for the update, once for the version that worked


def test_an_update_notes_why_before_it_restarts_the_bridge(install, tmp_path):
    origin, clone = install
    before = git(clone, "rev-parse", "HEAD")
    new = commit(origin, "b")
    service = Service()
    run(clone, tmp_path, service)
    assert service.said == [("update", before, new), "restart"]


def test_a_rollback_notes_why_before_it_restarts_the_bridge(install, tmp_path):
    origin, clone = install
    before = git(clone, "rev-parse", "HEAD")
    new = commit(origin, "b")
    service = Service(healthy=False)
    run(clone, tmp_path, service)
    assert service.said == [("update", before, new), "restart", ("rollback", new, before), "restart"]


def test_an_update_whose_tests_fail_notes_nothing(install, tmp_path):
    origin, clone = install
    commit(origin, "b")
    service = Service()
    run(clone, tmp_path, service, tests_pass=False)
    assert service.said == []


def test_only_one_update_runs_at_a_time(install, tmp_path):
    _, clone = install
    log = tmp_path / "update.json"
    log.write_text(json.dumps({"running": True, "pid": __import__("os").getpid(), "steps": []}))
    assert updater.running(log) is True
    log.write_text(json.dumps({"running": True, "pid": 999999999, "steps": []}))  # a run that died
    assert updater.running(log) is False


# -- through the bridge -----------------------------------------------------------------


import httpx  # noqa: E402
from fakes import FakeClaude  # noqa: E402
from mcp.client import Client  # noqa: E402

from claude_voice import api  # noqa: E402
from claude_voice.oauth import OAuthProvider  # noqa: E402
from claude_voice.server import build_app, build_server  # noqa: E402
from claude_voice.sessions import SessionManager  # noqa: E402
from claude_voice.store import Store  # noqa: E402

SECRET = "s3cret-token-for-the-update-tests-x"
BEARER = {"Authorization": f"Bearer {SECRET}"}


class Launcher:
    def __init__(self):
        self.started = []

    def __call__(self, repo, log):
        self.started.append((repo, log))


def bridge_with_update(tmp_path, launcher):
    (tmp_path / "home").mkdir(exist_ok=True)
    store = Store(tmp_path / "b.db")
    oauth = OAuthProvider(store, SECRET, "http://127.0.0.1:8811", static_token=SECRET)
    m = SessionManager(store, project_root=tmp_path / "home", client_factory=FakeClaude())
    server = build_server(
        m,
        oauth=oauth,
        conversations=lambda **k: [],
        live_dir=tmp_path / "live",
        update_log=tmp_path / "update.json",
        launch_update=launcher,
        bridge_folder=tmp_path,
    )
    return server, build_app(server)


def client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8811")


async def test_the_app_and_the_voice_start_an_update_that_runs_outside_the_bridge(tmp_path):
    launcher = Launcher()
    server, app = bridge_with_update(tmp_path, launcher)
    async with client(app) as c:
        started = await c.post("/api/v1/update", headers=BEARER)
        state = (await c.get("/api/v1/update", headers=BEARER)).json()
    assert started.status_code == 202 and launcher.started == [(tmp_path, tmp_path / "update.json")]
    assert "drops" in started.json()["note"]
    assert state["running"] is False and state["steps"] == []  # the launcher here writes nothing
    async with Client(server) as mcp:
        out = (await mcp.call_tool("update_bridge", {})).structured_content
    assert out["started"] is True and len(launcher.started) == 2


async def test_a_second_update_is_refused_while_one_runs(tmp_path):
    import os

    launcher = Launcher()
    (tmp_path / "update.json").write_text(json.dumps({"running": True, "pid": os.getpid(), "steps": []}))
    _, app = bridge_with_update(tmp_path, launcher)
    async with client(app) as c:
        again = await c.post("/api/v1/update", headers=BEARER)
    assert again.status_code == 409 and launcher.started == []


async def test_each_step_of_an_update_reaches_open_streams(tmp_path):
    launcher = Launcher()
    server, app = bridge_with_update(tmp_path, launcher)
    log = tmp_path / "update.json"
    with api.HUB.subscribe() as events:
        async with client(app) as c:
            log.write_text(
                json.dumps(
                    {"running": True, "pid": 1, "steps": [{"step": "check", "ok": True, "detail": "", "at": "t"}]}
                )
            )
            await c.get("/api/v1/update", headers=BEARER)
            log.write_text(
                json.dumps(
                    {
                        "running": False,
                        "pid": 1,
                        "result": "up_to_date",
                        "steps": [
                            {"step": "check", "ok": True, "detail": "", "at": "t"},
                            {"step": "done", "ok": True, "detail": "", "at": "t"},
                        ],
                    }
                )
            )
            await c.get("/api/v1/update", headers=BEARER)
        seen = []
        while not events.empty():
            seen.append(events.get_nowait())
    steps = [d["step"] for name, d in seen if name == "update"]
    assert steps == ["check", "done"]


async def test_the_bridge_says_which_version_runs_and_where_its_folder_stands(tmp_path):
    _, app = bridge_with_update(tmp_path, Launcher())
    async with client(app) as c:
        out = (await c.get("/api/v1/bridge", headers=BEARER)).json()
    assert set(out) == {"version", "git", "started_at"}


async def test_the_update_runs_in_the_clone_the_settings_name(tmp_path):
    from claude_voice import config

    launcher = Launcher()
    clone = tmp_path / "my-cv"
    clone.mkdir()
    settings = tmp_path / "config.toml"
    settings.write_text(f"[paths]\nclaude_voice = '{clone}'\n")
    (tmp_path / "home").mkdir(exist_ok=True)
    store = Store(tmp_path / "b.db")
    m = SessionManager(store, project_root=tmp_path / "home", client_factory=FakeClaude())
    server = build_server(
        m,
        conversations=lambda **k: [],
        live_dir=tmp_path / "live",
        update_log=tmp_path / "update.json",
        launch_update=launcher,
        repo_config=config.load(settings, own_clone=None, env={}),
    )
    async with Client(server) as mcp:
        await mcp.call_tool("update_bridge", {})
    assert launcher.started[0][0] == clone


def test_the_updater_reaches_github_over_https_not_the_owners_ssh_key(monkeypatch, tmp_path):
    """The service cannot reach the owner's ssh-agent, so GitHub's ssh addresses are read over HTTPS."""
    seen = []

    def run(command, **kwargs):
        seen.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(updater.subprocess, "run", run)
    updater._git(tmp_path, "fetch", "--quiet")
    assert seen[0][1:5] == ["-c", "url.https://github.com/.insteadOf=git@github.com:", "-C", str(tmp_path)]


def test_a_refused_fetch_says_why_and_what_to_do():
    said = updater.fetch_failure("git@github.com: Permission denied (publickey).\nfatal: Could not read")
    assert "GitHub refused" in said and "private" in said
    assert (
        updater.fetch_failure("fatal: unable to access: Could not resolve host")
        == "Could not fetch the latest version."
    )
