"""What an update would bring: the new commits on GitHub, grouped by kind, with no model involved."""

import subprocess

import httpx
from fakes import FakeClaude
from mcp.client import Client

from claude_voice import updater
from claude_voice.oauth import OAuthProvider
from claude_voice.server import build_app, build_server
from claude_voice.sessions import SessionManager
from claude_voice.store import Store

SECRET = "s3cret-token-for-the-change-tests-xx"
BEARER = {"Authorization": f"Bearer {SECRET}"}
HOST = "http://127.0.0.1:8811"


def test_new_commits_are_grouped_by_kind_new_things_first():
    groups = updater.group_commits(
        [
            ("a1", "fix(api): Refuse an empty answer"),
            ("b2", "feat: Answer questions from the phone"),
            ("c3", "test: Describe answering from the phone"),
            ("d4", "Merge something odd"),
            ("e5", "feat!: Drop the old event names"),
        ]
    )
    assert [g["type"] for g in groups] == ["feat", "fix", "test", "other"]
    assert groups[0]["title"] == "New"
    assert [c["subject"] for c in groups[0]["commits"]] == [
        "Answer questions from the phone",
        "Drop the old event names",
    ]
    assert groups[0]["commits"][1]["breaking"] is True
    assert groups[3]["commits"][0]["subject"] == "Merge something odd"


def git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def clones(tmp_path):
    """A repository on 'GitHub' with two commits the local clone has not pulled yet."""
    origin, local = tmp_path / "origin", tmp_path / "local"
    origin.mkdir()
    git(origin, "init", "-q", "-b", "main")
    git(origin, "config", "user.email", "t@example.com")
    git(origin, "config", "user.name", "T")
    git(origin, "commit", "-q", "--allow-empty", "-m", "chore: Start")
    subprocess.run(["git", "clone", "-q", str(origin), str(local)], check=True, capture_output=True)
    git(origin, "commit", "-q", "--allow-empty", "-m", "feat: Give each session a page")
    git(origin, "commit", "-q", "--allow-empty", "-m", "fix: Show a background command as idle")
    return local


def test_the_changes_are_the_commits_on_github_not_yet_here(tmp_path):
    out = updater.changes(clones(tmp_path))
    assert out["behind"] == 2 and out["current"] and out["latest"] and out["current"] != out["latest"]
    assert [g["type"] for g in out["groups"]] == ["feat", "fix"]


def test_an_up_to_date_clone_has_no_changes(tmp_path):
    local = clones(tmp_path)
    git(local, "pull", "-q")
    out = updater.changes(local)
    assert out["behind"] == 0 and out["groups"] == []


def bridge(tmp_path, folder):
    store = Store(tmp_path / "b.db")
    m = SessionManager(store, project_root=tmp_path, client_factory=FakeClaude())
    return build_server(
        m,
        oauth=OAuthProvider(store, SECRET, HOST, static_token=SECRET),
        conversations=lambda **k: [],
        live_dir=tmp_path / "live",
        bridge_folder=folder,
    )


async def test_the_app_and_the_voice_can_ask_what_an_update_would_bring(tmp_path):
    server = bridge(tmp_path, clones(tmp_path))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=build_app(server)), base_url=HOST) as c:
        page = (await c.get("/api/v1/update/changes", headers=BEARER)).json()
    async with Client(server) as mcp:
        spoken = (await mcp.call_tool("update_changes", {})).structured_content
    assert page["behind"] == 2 and page["groups"][0]["commits"][0]["subject"] == "Give each session a page"
    assert spoken["behind"] == 2
