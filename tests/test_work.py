"""The team's work on one page: what waits on the owner, and every open pull request."""

import json
import subprocess

import httpx
from fakes import FakeClaude

from claude_voice import config, work
from claude_voice.oauth import OAuthProvider
from claude_voice.server import build_app, build_server
from claude_voice.sessions import SessionManager
from claude_voice.store import Store

SECRET = "s3cret-token-for-the-work-tests"
BEARER = {"Authorization": f"Bearer {SECRET}"}
HOST = "http://127.0.0.1:8811"


def write_waiting(path, items):
    path.write_text(json.dumps(items), encoding="utf-8")
    return path


def test_the_owners_list_is_read_as_the_manager_wrote_it_and_a_broken_item_is_left_out(tmp_path):
    path = write_waiting(
        tmp_path / "waiting.json",
        [
            {
                "id": "settings",
                "text": "Run the settings script.",
                "command": "! python3 x",
                "since": "2026-11-10T18:00:00+00:00",
            },
            {"id": "rename", "text": "Rename the vacuum's start?", "repo": "dreame-mcp"},
            {"text": "no id, so left out"},
            {"id": "bad", "text": 5},
            "not an item",
        ],
    )
    items = work.waiting(path)
    assert [i["id"] for i in items] == ["settings", "rename"]
    assert items[0]["command"] == "! python3 x" and items[0]["repo"] is None
    assert items[1]["repo"] == "dreame-mcp" and items[1]["command"] is None and items[1]["since"]


def test_a_missing_or_unreadable_list_is_an_empty_one(tmp_path):
    assert work.waiting(tmp_path / "nothing.json") == []
    (tmp_path / "broken.json").write_text("{not json", encoding="utf-8")
    assert work.waiting(tmp_path / "broken.json") == []


def test_a_pull_requests_checks_are_one_word():
    done = {"__typename": "CheckRun", "status": "COMPLETED", "conclusion": "SUCCESS"}
    skipped = {"__typename": "CheckRun", "status": "COMPLETED", "conclusion": "SKIPPED"}
    running = {"__typename": "CheckRun", "status": "IN_PROGRESS", "conclusion": ""}
    failed = {"__typename": "CheckRun", "status": "COMPLETED", "conclusion": "FAILURE"}
    old_style = {"__typename": "StatusContext", "state": "ERROR"}
    assert work.checks([done, skipped]) == "pass"
    assert work.checks([done, running]) == "pending"
    assert work.checks([done, failed, running]) == "fail"
    assert work.checks([old_style]) == "fail"
    assert work.checks([]) == "pending"


def test_open_pull_requests_come_from_every_repository_and_one_that_cannot_be_read_is_named():
    listed = [
        {
            "number": 6,
            "title": "Mark the read-only tools",
            "url": "https://gh/6",
            "isDraft": False,
            "statusCheckRollup": [{"__typename": "CheckRun", "status": "COMPLETED", "conclusion": "SUCCESS"}],
        }
    ]

    def run(args):
        return json.dumps(listed) if "o/garage-door" in args else None

    prs, unreadable = work.open_prs({"garage-door": "o/garage-door", "tv-mcp": "o/tv-mcp"}, run=run)
    assert prs == [
        {
            "repo": "garage-door",
            "number": 6,
            "title": "Mark the read-only tools",
            "url": "https://gh/6",
            "checks": "pass",
            "draft": False,
        }
    ]
    assert unreadable == ["tv-mcp"]


def git(folder, *args):
    subprocess.run(["git", "-C", str(folder), *args], check=True, capture_output=True)


def test_a_plugins_repository_is_the_one_its_branch_pushes_to(tmp_path):
    fork = tmp_path / "dreame"
    fork.mkdir()
    git(fork, "init", "-q", "-b", "voice")
    git(fork, "remote", "add", "origin", "https://github.com/upstream/dreame-mcp.git")
    git(fork, "remote", "add", "fork", "git@github.com:owner/dreame-mcp.git")
    git(fork, "config", "branch.voice.remote", "fork")
    plain = tmp_path / "garage"
    plain.mkdir()
    git(plain, "init", "-q", "-b", "main")
    git(plain, "remote", "add", "origin", "https://github.com/owner/garage-door")
    none = tmp_path / "loose"
    none.mkdir()
    spec = lambda name, cwd: config.PluginSpec(name, "x", [], str(cwd), ["t"])  # noqa: E731
    found = work.plugin_repos([spec("vacuum", fork), spec("garage", plain), spec("loose", none)])
    assert found == [("dreame-mcp", "owner/dreame-mcp", fork), ("garage-door", "owner/garage-door", plain)]


def test_the_page_only_changes_when_the_work_does(tmp_path):
    path = write_waiting(tmp_path / "waiting.json", [{"id": "a", "text": "One thing."}])
    calls = []

    def run(args):
        calls.append(args)
        return "[]"

    board = work.Board({"r": "o/r"}, run, waiting_file=path)
    first = board.report()
    assert first["waiting_on_owner"][0]["id"] == "a" and first["open_prs"] == [] and first["unreadable"] == []
    assert board.changed() is None  # nothing new since the last look
    write_waiting(path, [])
    assert board.changed()["waiting_on_owner"] == []  # the owner's list is read fresh each time
    board.report()
    assert len(calls) == 1  # pull requests are asked for at most once a minute


async def test_the_phone_page_reads_the_work_with_the_token_and_not_without(tmp_path):
    path = write_waiting(tmp_path / "waiting.json", [{"id": "a", "text": "One thing."}])
    store = Store(tmp_path / "b.db")
    m = SessionManager(store, project_root=tmp_path, client_factory=FakeClaude())
    server = build_server(
        m,
        oauth=OAuthProvider(store, SECRET, HOST, static_token=SECRET),
        conversations=lambda **k: [],
        live_dir=tmp_path / "live",
        repo_config=config.Config(config.Repos(), config.Paths(None, None, None, None), None),
        kept_levels=lambda repo, **_: [],
        work_board=work.Board({}, lambda args: "[]", waiting_file=path),
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=build_app(server)), base_url=HOST) as c:
        refused = await c.get("/api/v1/work")
        page = (await c.get("/api/v1/work", headers=BEARER)).json()
    assert refused.status_code == 401
    assert page["waiting_on_owner"][0]["text"] == "One thing." and page["as_of"]


async def test_the_work_page_says_whether_developer_mode_is_on(tmp_path, monkeypatch):
    from claude_voice import devmode

    monkeypatch.setattr(devmode, "STATE", tmp_path / "developer-mode.json")
    path = write_waiting(tmp_path / "waiting.json", [])
    store = Store(tmp_path / "b.db")
    m = SessionManager(store, project_root=tmp_path, client_factory=FakeClaude())
    server = build_server(
        m,
        oauth=OAuthProvider(store, SECRET, HOST, static_token=SECRET),
        conversations=lambda **k: [],
        live_dir=tmp_path / "live",
        repo_config=config.Config(config.Repos(), config.Paths(None, None, None, None), None),
        kept_levels=lambda repo, **_: [],
        work_board=work.Board({}, lambda args: "[]", waiting_file=path),
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=build_app(server)), base_url=HOST) as c:
        page = (await c.get("/api/v1/work", headers=BEARER)).json()
    assert page["developer_mode"] == {"on": False, "until": None}


def test_the_suite_never_asks_github():
    """A watched server reads the open pull requests between its looks at the sessions.
    Over the network that took seconds, and a test waiting for the next look ran out of time."""
    from claude_voice import software

    assert software._gh(["--version"]) is None
