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

    def restart(self):
        self.restarts += 1

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


def test_only_one_update_runs_at_a_time(install, tmp_path):
    _, clone = install
    log = tmp_path / "update.json"
    log.write_text(json.dumps({"running": True, "pid": __import__("os").getpid(), "steps": []}))
    assert updater.running(log) is True
    log.write_text(json.dumps({"running": True, "pid": 999999999, "steps": []}))  # a run that died
    assert updater.running(log) is False
