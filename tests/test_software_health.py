"""The software page: each repository's quality measures, their trend, and whether its checks pass."""

import asyncio
import json
import pathlib
import subprocess
import threading
import time

import httpx
from fakes import FakeClaude
from mcp.client import Client

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
        ci_status=lambda repo, **_: {"conclusion": "success", "status": "completed", "url": f"https://ci/{repo}"},
        kept_levels=lambda repo, **_: [],
        main_head=lambda repo, **_: "aaa",
    )
    await asyncio.to_thread(server.software_report.refresh)
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


def test_a_repository_with_no_time_budget_still_shows_how_long_its_tests_take(tmp_path):
    empty = tmp_path / "plugin"
    empty.mkdir()
    kept = [{**QUALITY, "tests": 470, "test_budget": None}]
    metrics = by_id(software.repo_report("tv-mcp", "https://github.com/o/tv-mcp", empty, kept=kept))
    assert metrics["tests"]["value"] == 470
    assert metrics["test_time"]["value"] == 103.4
    assert metrics["test_time"]["text"] == "103 s"
    assert metrics["test_time"]["status"] == "unknown"


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


def test_the_newest_levels_are_fetched_first_when_there_are_too_many_for_one_look(tmp_path):
    # A repository that kept no levels for its first runs must still show its newest ones at once,
    # not after an hour of looks spent downloading the old runs that kept nothing.
    runs = {i: None for i in range(1, 21)} | {i: {**QUALITY, "tests": i} for i in range(21, 24)}
    got = software.kept_levels("o/r", tmp_path / "cache", run=fake_gh(runs, []))
    assert [q["tests"] for q in got] == [21, 22, 23]


def test_levels_are_read_from_the_branch_the_plugin_follows(tmp_path):
    seen = []

    def run(args):
        if args[:2] == ["run", "list"]:
            seen.append(args[args.index("--branch") + 1])
        return "[]"

    software.kept_levels("o/r", tmp_path / "cache", run=run, branch="voice")
    assert seen == ["voice"]


def exemptions(repo, text):
    (repo / "quality.toml").write_text(text)
    return repo


EXEMPT = """
[[exempt]]
measure = "complexity"
reason = "the tool table is one long match"
approved = "Roy, 2026-11-11"
"""


def test_an_exempt_measure_is_shown_as_exempt_and_never_as_ok(tmp_path):
    repo = exemptions(repo_with_history(tmp_path / "r", QUALITY), EXEMPT)
    complexity = by_id(software.repo_report("r", "https://github.com/o/r", repo))["complexity"]
    assert complexity["status"] == "exempt"
    assert complexity["text"].endswith("exempt: the tool table is one long match")
    inside = exemptions(repo_with_history(tmp_path / "s", {**QUALITY, "complexity": {"a.py": 9}}), EXEMPT)
    assert by_id(software.repo_report("s", "https://github.com/o/s", inside))["complexity"]["status"] == "ok"


def test_an_exemption_counts_only_with_a_reason_and_the_owners_yes(tmp_path):
    no_yes = EXEMPT.replace('approved = "Roy, 2026-11-11"', "")
    no_reason = EXEMPT.replace('reason = "the tool table is one long match"', 'reason = "  "')
    for i, text in enumerate((no_yes, no_reason, "not toml [[")):
        repo = exemptions(repo_with_history(tmp_path / f"r{i}", QUALITY), text)
        assert by_id(software.repo_report("r", "https://github.com/o/r", repo))["complexity"]["status"] == "bad"


# -- the report the bridge keeps ---------------------------------------------------------

REPORT = {"as_of": "2026-11-11T08:00:00+00:00", "repos": [{"name": "claude-voice", "as_of": "x", "metrics": []}]}


class Builds:
    """A fake build of the report: counts its calls, and can fail or be held until let go."""

    def __init__(self, report=REPORT):
        self.report, self.calls, self.fail = report, 0, None
        self.began, self.go = threading.Event(), threading.Event()
        self.go.set()

    def __call__(self):
        self.calls += 1
        self.began.set()
        self.go.wait(5)
        if self.fail:
            raise self.fail
        return self.report


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def refresher(build, heads=dict, clock=None):
    return software.Refresher(build, heads, clock=clock or Clock())


def test_the_software_page_answers_at_once_from_the_report_it_keeps():
    build = Builds()
    kept = refresher(build)
    kept.refresh()
    answers = [kept.answer() for _ in range(3)]
    assert build.calls == 1  # three answers, one build
    page = answers[-1]
    assert page["refreshing"] is False and page["measured_at"] and page["as_of"]
    assert [r["name"] for r in page["repos"]] == ["claude-voice"]
    assert page["repos"][0]["measured_at"] == page["measured_at"]
    assert page["repos"][0]["as_of"] != "x"  # each answer is dated now; the measuring keeps its own time


def test_before_the_first_build_the_software_page_is_empty_and_says_it_is_refreshing():
    build = Builds()
    assert refresher(build).answer() == {"repos": [], "measured_at": None, "refreshing": True}
    assert build.calls == 0  # asking never builds


def test_a_failed_refresh_keeps_the_report_it_had(caplog):
    build, clock = Builds(), Clock()
    kept = refresher(build, clock=clock)
    kept.refresh()
    before = kept.answer()["measured_at"]
    build.fail = RuntimeError("gh: not signed in")
    clock.now += 601
    with caplog.at_level("WARNING", logger="claude_voice"):
        kept.refresh()
    page = kept.answer()
    assert [r["name"] for r in page["repos"]] == ["claude-voice"]
    assert page["measured_at"] == before and page["refreshing"] is False
    said = [r.getMessage() for r in caplog.records]
    assert len(said) == 1 and "gh: not signed in" in said[0] and "gh auth status" in said[0]


def test_only_one_software_build_runs_at_a_time():
    build = Builds()
    build.go.clear()
    kept = refresher(build)
    first = threading.Thread(target=kept.refresh)
    first.start()
    assert build.began.wait(5)
    assert kept.answer()["refreshing"] is True
    second = kept.refresh()  # returns at once: the build under way is the one that counts
    build.go.set()
    first.join(5)
    assert second is False and build.calls == 1


def test_a_moved_main_starts_a_new_build():
    build, clock, heads = Builds(), Clock(), {"o/r": "aaa"}
    kept = refresher(build, heads=lambda: dict(heads), clock=clock)
    kept.tick()  # the first build
    clock.now += 60
    kept.tick()  # nothing moved and not yet due
    assert build.calls == 1
    heads["o/r"] = "bbb"
    kept.tick()
    assert build.calls == 2
    heads["o/r"] = None  # GitHub not answering is not a move
    kept.tick()
    assert build.calls == 2


def test_a_build_is_due_ten_minutes_after_the_last():
    build, clock = Builds(), Clock()
    kept = refresher(build, clock=clock)
    kept.tick()
    clock.now += 599
    kept.tick()
    assert build.calls == 1
    clock.now += 1
    kept.tick()
    assert build.calls == 2


def test_the_first_build_runs_behind_and_answers_meanwhile_say_it_is_refreshing():
    build = Builds()
    build.go.clear()
    kept = refresher(build)
    asked = time.monotonic()
    stop = kept.start()
    assert time.monotonic() - asked < 0.5
    assert build.began.wait(5)
    assert kept.answer()["refreshing"] is True
    build.go.set()
    for _ in range(250):
        if kept.answer()["measured_at"]:
            break
        time.sleep(0.02)
    stop()
    assert kept.answer()["refreshing"] is False and kept.answer()["measured_at"]


def test_the_head_of_main_is_read_from_github():
    seen = []

    def run(args):
        seen.append(args)
        return "abc123\n"

    assert software.head("o/r", "voice", run=run) == "abc123"
    assert "repos/o/r/commits/voice" in seen[0]
    assert software.head("o/r", run=lambda args: None) is None


async def test_the_bridge_starts_listening_while_the_software_report_is_built(tmp_path):
    voice = repo_with_history(tmp_path / "voice", QUALITY)
    held = threading.Event()

    def slow_ci(repo, **_):
        held.wait(5)
        return {"conclusion": "success", "status": "completed", "url": f"https://ci/{repo}"}

    m = SessionManager(Store(tmp_path / "b.db"), project_root=tmp_path, client_factory=FakeClaude())
    server = build_server(
        m,
        live_dir=tmp_path / "live",
        projects_dir=tmp_path / "projects",
        watch_interval=60,
        repo_config=config.Config(config.Repos(), config.Paths(voice, None, None, None), None),
        ci_status=slow_ci,
        kept_levels=lambda repo, **_: [],
        main_head=lambda repo, **_: "aaa",
    )
    asked = time.monotonic()
    async with Client(server) as client:
        await client.call_tool("list_pending_approvals", {})
        answered = time.monotonic() - asked
        assert server.software_report.answer()["refreshing"] is True
        held.set()
        for _ in range(200):
            if server.software_report.answer()["measured_at"]:
                break
            await asyncio.sleep(0.05)
    assert answered < 1.5
    assert server.software_report.answer()["repos"][0]["name"] == "claude-voice"
