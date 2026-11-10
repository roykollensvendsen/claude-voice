"""The software page: each repository's quality measures, their trend, and whether its checks pass."""

import json
import pathlib
import subprocess

import httpx
from fakes import FakeClaude

from claude_voice import config, software
from claude_voice.oauth import OAuthProvider
from claude_voice.server import build_app, build_server
from claude_voice.sessions import SessionManager
from claude_voice.store import Store

SECRET = "s3cret-token-for-the-software-tests"
BEARER = {"Authorization": f"Bearer {SECRET}"}
HOST = "http://127.0.0.1:8811"

QUALITY = {
    "coverage": 91.34,
    "mutation": {"killed": 24, "total": 24},
    "tests": 468,
    "lint_errors": 0,
    "type_errors": 0,
    "cycles": 0,
    "complexity": {"pkg/a.py": 12, "pkg/server.py": 52},
    "test_seconds": 103.4,
    "test_budget": {"seconds": 180, "tolerance": 0.25},
}


def git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def repo_with_history(path, *versions):
    """A repository whose quality.json went through `versions`, one commit each."""
    path.mkdir()
    git(path, "init", "-q", "-b", "main")
    git(path, "config", "user.email", "t@example.com")
    git(path, "config", "user.name", "T")
    for version in versions:
        (path / "quality.json").write_text(json.dumps(version))
        git(path, "add", "quality.json")
        git(path, "commit", "-q", "-m", "chore: Record the levels")
    return path


def by_id(repo_report):
    return {m["id"]: m for m in repo_report["metrics"]}


def test_each_measure_has_a_value_a_status_and_its_trend_oldest_first(tmp_path):
    older = {**QUALITY, "coverage": 88.0, "tests": 450}
    repo = repo_with_history(tmp_path / "r", older, QUALITY)
    report = software.repo_report("claude-voice", "https://github.com/o/claude-voice", repo, ci=None)
    metrics = by_id(report)
    assert metrics["coverage"]["value"] == 91.34 and metrics["coverage"]["status"] == "ok"
    assert metrics["coverage"]["trend"] == [88.0, 91.34]
    assert metrics["tests"]["trend"] == [450, 468]
    assert metrics["mutation"]["value"] == 100.0 and metrics["mutation"]["text"] == "24 of 24 rules"
    assert metrics["complexity"]["value"] == 52 and "server.py" in metrics["complexity"]["text"]
    assert metrics["complexity"]["status"] == "bad"
    assert metrics["test_time"]["status"] == "ok" and metrics["test_time"]["unit"] == "s"
    assert metrics["coverage"]["url"] == "https://github.com/o/claude-voice/blob/main/quality.json"
    assert report["as_of"]


def test_the_trend_keeps_the_newest_thirty(tmp_path):
    versions = [{**QUALITY, "tests": 400 + i} for i in range(35)]
    metrics = by_id(software.repo_report("r", "https://github.com/o/r", repo_with_history(tmp_path / "r", *versions)))
    assert len(metrics["tests"]["trend"]) == 30 and metrics["tests"]["trend"][-1] == 434


def test_a_repository_without_measures_says_so_rather_than_guessing(tmp_path):
    empty = tmp_path / "e"
    empty.mkdir()
    report = software.repo_report("claude-voice-ui", "https://github.com/o/ui", empty, ci=None)
    assert {m["status"] for m in report["metrics"]} == {"unknown"}


def test_the_checks_on_main_are_a_measure_too():
    passing = software.ci_metric({"conclusion": "success", "status": "completed", "url": "https://x/run/1"})
    failing = software.ci_metric({"conclusion": "failure", "status": "completed", "url": "https://x/run/2"})
    running = software.ci_metric({"conclusion": "", "status": "in_progress", "url": "https://x/run/3"})
    assert (passing["status"], failing["status"], running["status"]) == ("ok", "bad", "unknown")
    assert passing["url"] == "https://x/run/1"


async def test_the_app_reads_both_repositories_on_one_page(tmp_path):
    voice = repo_with_history(tmp_path / "voice", QUALITY)
    ui = repo_with_history(tmp_path / "ui", {**QUALITY, "coverage": 55.0})
    store = Store(tmp_path / "b.db")
    m = SessionManager(store, project_root=tmp_path, client_factory=FakeClaude())
    repos = config.Config(config.Repos(), config.Paths(voice, ui, None, None), None)
    server = build_server(
        m,
        oauth=OAuthProvider(store, SECRET, HOST, static_token=SECRET),
        conversations=lambda **k: [],
        live_dir=tmp_path / "live",
        repo_config=repos,
        ci_status=lambda repo: {"conclusion": "success", "status": "completed", "url": f"https://ci/{repo}"},
        kept_levels=lambda repo: [],
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=build_app(server)), base_url=HOST) as c:
        page = (await c.get("/api/v1/health/software", headers=BEARER)).json()
    names = [r["name"] for r in page["repos"]]
    assert names == ["claude-voice", "claude-voice-ui"] and page["as_of"]
    assert by_id(page["repos"][1])["coverage"]["status"] == "bad"
    assert by_id(page["repos"][0])["ci"]["url"] == "https://ci/roykollensvendsen/claude-voice"


def test_layering_and_duplication_are_measures_once_a_repository_records_them(tmp_path):
    repo = repo_with_history(tmp_path / "r", {**QUALITY, "layer_violations": 0, "duplication": 2.45})
    metrics = by_id(software.repo_report("r", "https://github.com/o/r", repo))
    assert metrics["layers"]["status"] == "ok" and metrics["layers"]["text"] == "every module keeps to its layer"
    assert metrics["duplication"]["value"] == 2.45 and metrics["duplication"]["status"] == "ok"
    older = by_id(software.repo_report("r", "https://github.com/o/r", repo_with_history(tmp_path / "s", QUALITY)))
    assert older["layers"]["status"] == "unknown"


def test_the_levels_main_kept_are_the_value_now_and_extend_the_trend(tmp_path):
    older = {**QUALITY, "tests": 450}
    repo = repo_with_history(tmp_path / "r", older, {"test_budget": {"seconds": 200, "tolerance": 0.25}})
    kept = [{**QUALITY, "tests": 460, "test_budget": None}, {**QUALITY, "tests": 470, "test_budget": None}]
    report = software.repo_report("claude-voice", "https://github.com/o/claude-voice", repo, kept=kept)
    metrics = by_id(report)
    assert metrics["tests"]["value"] == 470
    assert metrics["tests"]["trend"] == [450, 460, 470]
    assert "200 s budget" in metrics["test_time"]["text"]


def fake_gh(levels_by_run, calls):
    """GitHub's command line as far as kept_levels uses it: listing main's runs and downloading one's levels."""

    def run(args):
        calls.append(args[:2])
        if args[:2] == ["run", "list"]:
            assert args[args.index("--status") + 1] == "completed"  # a running run has kept nothing yet
            return json.dumps([{"databaseId": i} for i in sorted(levels_by_run, reverse=True)])
        if args[:2] == ["run", "download"]:
            levels = levels_by_run[int(args[2])]
            if levels is None:
                return None
            folder = pathlib.Path(args[args.index("--dir") + 1])
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "levels.json").write_text(json.dumps(levels))
            return ""
        raise AssertionError(args)

    return run


def test_kept_levels_are_fetched_from_mains_runs_once_and_then_read_from_disk(tmp_path):
    runs = {101: {**QUALITY, "tests": 1}, 102: None, 103: {**QUALITY, "tests": 3}}
    calls = []
    first = software.kept_levels("o/r", tmp_path / "cache", run=fake_gh(runs, calls))
    assert [q["tests"] for q in first] == [1, 3]  # oldest first, a run with no levels skipped
    calls.clear()
    again = software.kept_levels("o/r", tmp_path / "cache", run=fake_gh(runs, calls))
    assert again == first and calls == [["run", "list"]]
