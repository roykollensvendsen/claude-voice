"""Git status for a session's folder: branch, commit, uncommitted changes, and how far ahead or behind."""

import json
import os
import subprocess

import pytest
from fakes import FakeClaude
from mcp.client import Client

from claude_voice import gitstatus
from claude_voice.server import build_server
from claude_voice.sessions import SessionManager
from claude_voice.store import Store


def git(folder, *args):
    subprocess.run(["git", "-C", str(folder), *args], check=True, capture_output=True)


def commit(folder, name, text="x"):
    (folder / name).write_text(text)
    git(folder, "add", name)
    git(folder, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", f"Add {name}")


@pytest.fixture
def repos(tmp_path):
    """An origin with a main branch, and a clone of it working on a feature branch."""
    origin = tmp_path / "origin"
    origin.mkdir()
    git(origin, "init", "-q", "-b", "main")
    commit(origin, "a")
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", str(origin), str(clone)], check=True)
    return origin, clone


def test_a_folder_that_is_not_a_repository_has_no_git_status(tmp_path):
    assert gitstatus.read(tmp_path) is None


def test_the_branch_commit_and_uncommitted_changes_are_read(repos):
    _, clone = repos
    git(clone, "switch", "-q", "-c", "feat/x")
    commit(clone, "b")
    (clone / "loose").write_text("y")
    out = gitstatus.read(clone)
    assert out["branch"] == "feat/x" and out["commit"]["subject"] == "Add b" and len(out["commit"]["hash"]) >= 7
    assert out["dirty"] is True and out["changed_files"] == 1
    assert out["tracking"] is None  # never pushed
    assert out["main"] == {"name": "origin/main", "ahead": 1, "behind": 0}
    assert out["parent"] is None


def test_ahead_and_behind_are_counted_against_the_tracked_branch_and_main(repos):
    origin, clone = repos
    commit(origin, "c")  # main moves on at the origin
    git(clone, "fetch", "-q")
    commit(clone, "d")
    out = gitstatus.read(clone)
    assert out["branch"] == "main" and out["tracking"] == {"name": "origin/main", "ahead": 1, "behind": 1}
    assert out["main"] == {"name": "origin/main", "ahead": 1, "behind": 1}


def test_a_fork_is_compared_with_its_parent_too(repos, tmp_path):
    origin, clone = repos
    git(clone, "remote", "add", "upstream", str(origin))
    commit(origin, "e")
    git(clone, "fetch", "-q", "upstream")
    out = gitstatus.read(clone)
    assert out["parent"] == {"name": "upstream/main", "ahead": 0, "behind": 1}


def test_a_detached_head_has_no_branch(repos):
    _, clone = repos
    git(clone, "switch", "-q", "--detach")
    assert gitstatus.read(clone)["branch"] is None


async def test_every_session_in_the_list_carries_its_folders_git_status(repos, tmp_path):
    _, clone = repos
    live = tmp_path / "live"
    live.mkdir()
    (live / "x.json").write_text(
        json.dumps({"pid": os.getpid(), "sessionId": "s1", "cwd": str(clone), "name": "clone-ab", "status": "idle"})
    )
    m = SessionManager(Store(tmp_path / "b.db"), project_root=tmp_path, client_factory=FakeClaude())
    server = build_server(m, conversations=lambda **k: [], live_dir=live, projects_dir=tmp_path / "projects")
    async with Client(server) as c:
        rows = (await c.call_tool("fleet_recap", {})).structured_content["sessions"]
    row = next(r for r in rows if r["name"] == "clone-ab")
    assert row["git"]["branch"] == "main" and row["git"]["commit"]["subject"] == "Add a"
