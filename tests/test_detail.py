"""A session's own page: how full it is, what it used, its helpers, what happened, and a summary made on request."""

import asyncio

import httpx
import pytest
from fakes import FakeClaude
from mcp.client import Client
from test_fleet import running, transcript, world  # noqa: F401  (fixtures)

from claude_voice import api, detail
from claude_voice.oauth import OAuthProvider
from claude_voice.server import build_app, build_server
from claude_voice.sessions import SessionManager
from claude_voice.store import Store

SECRET = "s3cret-token-for-the-detail-tests-x"
BEARER = {"Authorization": f"Bearer {SECRET}"}
HOST = "http://127.0.0.1:8811"


def at(minute):
    return f"2026-11-02T10:{minute:02d}:00.000Z"


def prompt(text, minute):
    return {"type": "user", "timestamp": at(minute), "message": {"role": "user", "content": text}}


def tool(name, minute, tool_id, **given):
    return {
        "type": "assistant",
        "timestamp": at(minute),
        "message": {"content": [{"type": "tool_use", "id": tool_id, "name": name, "input": given}]},
    }


def result(tool_id, minute, text="ok", error=False, **extra):
    return {
        "type": "user",
        "timestamp": at(minute),
        "message": {"content": [{"type": "tool_result", "tool_use_id": tool_id, "content": text, "is_error": error}]},
        **extra,
    }


ENTRIES = [
    prompt("Make the export faster", 1),
    tool("Read", 2, "r1", file_path="/x/export.py"),
    result("r1", 2),
    tool("Bash", 3, "b1", command="uv run pytest -q"),
    result("b1", 3, "2 failed", error=True),
    tool("Agent", 4, "a1", subagent_type="Explore", run_in_background=True),
    result("a1", 4, toolUseResult={"agentId": "agent-1", "status": "async_launched", "isAsync": True}),
    tool("Agent", 5, "a2", subagent_type="Explore", run_in_background=True),
    result("a2", 5, toolUseResult={"agentId": "agent-2", "status": "async_launched", "isAsync": True}),
    {
        "type": "user",
        "timestamp": at(6),
        "isMeta": True,
        "origin": {"kind": "peer", "senderTaskId": "agent-1", "handback": True},
        "message": {"content": "[Subagent hand-back] done"},
    },
    tool("AskUserQuestion", 7, "q1", questions=[{"question": "Keep the old format?", "options": []}]),
    result("q1", 7),
    tool("Bash", 8, "b2", command='git commit -q -m "perf: Stream the export"'),
    result("b2", 8),
    {"type": "pr-link", "timestamp": at(9), "prNumber": 12, "prUrl": "https://github.com/o/r/pull/12"},
    tool("Bash", 10, "b3", command="gh pr merge 12 --rebase"),
    result("b3", 10),
    {"type": "system", "subtype": "compact_boundary", "timestamp": at(11), "content": "Conversation compacted"},
    {"type": "user", "timestamp": at(12), "message": {"content": "<task-notification>x</task-notification>"}},
]


def test_the_timeline_names_what_happened_newest_first():
    got = detail.digest(ENTRIES)
    kinds = [item["kind"] for item in got["timeline"]]
    assert kinds == ["compact", "pr_merged", "pr_opened", "commit", "question", "error", "prompt"]
    by_kind = {item["kind"]: item for item in got["timeline"]}
    assert by_kind["prompt"]["text"] == "Make the export faster"
    assert by_kind["commit"]["text"] == "perf: Stream the export"
    assert by_kind["pr_opened"]["url"] == "https://github.com/o/r/pull/12" and by_kind["pr_merged"]["text"] == "#12"
    assert by_kind["question"]["text"] == "Keep the old format?"
    assert by_kind["prompt"]["at"] == at(1)


def test_the_tool_calls_are_counted_per_tool():
    calls = detail.digest(ENTRIES)["tool_calls"]
    assert calls["total"] == 7 and calls["by_tool"]["Bash"] == 3 and calls["by_tool"]["Agent"] == 2


def test_a_helper_counts_as_running_until_it_has_handed_back():
    assert detail.digest(ENTRIES)["subagents"] == {"running": 1, "total": 2}


def test_the_timeline_keeps_the_newest_fifty():
    many = [prompt(f"step {i}", i % 60) for i in range(80)]
    timeline = detail.digest(many)["timeline"]
    assert len(timeline) == 50 and timeline[0]["text"] == "step 79"


# -- through the API and the voice ------------------------------------------------------


class Summarizer:
    def __init__(self, fail=False):
        self.calls, self.fail = [], fail
        self.release = asyncio.Event()

    async def __call__(self, text, question, language):
        self.calls.append((text, language))
        await self.release.wait()
        if self.fail:
            raise RuntimeError("model unavailable")
        return "It made the export faster and opened pull request 12."


def bridge(world, summarizer):  # noqa: F811
    running(world, "billing-ab", "billing", "s1", status="idle")
    transcript(world, "billing", "s1", ENTRIES)
    store = Store(world / "b.db")
    m = SessionManager(store, project_root=world / "home", client_factory=FakeClaude())
    return build_server(
        m,
        oauth=OAuthProvider(store, SECRET, HOST, static_token=SECRET),
        conversations=lambda **k: [],
        live_dir=world / "live",
        projects_dir=world / "projects",
        summarize=summarizer,
    )


def client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=HOST)


async def test_the_page_never_makes_a_summary_on_its_own(world):  # noqa: F811
    summarizer = Summarizer()
    async with client(build_app(bridge(world, summarizer))) as c:
        page = (await c.get("/api/v1/sessions/billing-ab/detail", headers=BEARER)).json()
    assert summarizer.calls == []
    assert page["summary"] is None and page["summary_state"] == "none"
    assert page["subagents"] == {"running": 1, "total": 2} and page["timeline"][0]["kind"] == "compact"
    assert page["number"] == 1 and page["name"] == "billing-ab"


async def test_a_second_request_for_a_summary_joins_the_one_already_running(world):  # noqa: F811
    summarizer = Summarizer()
    async with client(build_app(bridge(world, summarizer))) as c:
        with api.HUB.subscribe() as events:
            first = await c.post("/api/v1/sessions/billing-ab/summary", json={}, headers=BEARER)
            second = await c.post("/api/v1/sessions/billing-ab/summary", json={}, headers=BEARER)
            during = (await c.get("/api/v1/sessions/billing-ab/detail", headers=BEARER)).json()
            summarizer.release.set()
            for _ in range(50):
                await asyncio.sleep(0.02)
                after = (await c.get("/api/v1/sessions/billing-ab/detail", headers=BEARER)).json()
                if after["summary_state"] == "ready":
                    break
            seen = []
            while not events.empty():
                seen.append(events.get_nowait())
    assert first.status_code == 202 and second.status_code == 202 and first.json()["state"] == "generating"
    assert len(summarizer.calls) == 1  # a second tap joins the one already running
    assert during["summary_state"] == "generating"
    assert after["summary"].startswith("It made the export faster") and after["summary_at"]
    assert ("summary", {"session": "s1", "summary": after["summary"], "summary_state": "ready"}) in seen


async def test_a_summary_that_fails_says_so_and_keeps_the_last_one(world):  # noqa: F811
    summarizer = Summarizer(fail=True)
    summarizer.release.set()
    async with client(build_app(bridge(world, summarizer))) as c:
        await c.post("/api/v1/sessions/billing-ab/summary", json={}, headers=BEARER)
        for _ in range(50):
            await asyncio.sleep(0.02)
            page = (await c.get("/api/v1/sessions/billing-ab/detail", headers=BEARER)).json()
            if page["summary_state"] == "error":
                break
    assert page["summary_state"] == "error" and "unavailable" in page["summary_error"] and page["summary"] is None


async def test_an_unknown_session_has_no_page(world):  # noqa: F811
    async with client(build_app(bridge(world, Summarizer()))) as c:
        missing = await c.get("/api/v1/sessions/nobody/detail", headers=BEARER)
    assert missing.status_code == 404


async def test_the_voice_can_refresh_the_summary_of_the_chosen_session(world):  # noqa: F811
    summarizer = Summarizer()
    summarizer.release.set()
    server = bridge(world, summarizer)
    async with Client(server) as c:
        await c.call_tool("set_active_session", {"session": "billing-ab"})
        out = (await c.call_tool("refresh_session_summary", {"language": "norsk"})).structured_content
    assert out["session"] == "billing-ab" and out["summary"].startswith("It made the export faster")
    assert summarizer.calls[0][1] == "norsk"


@pytest.mark.parametrize("language", [None, "norsk"])
async def test_the_summary_is_asked_for_in_the_language_given(world, language):  # noqa: F811
    summarizer = Summarizer()
    summarizer.release.set()
    async with client(build_app(bridge(world, summarizer))) as c:
        body = {"language": language} if language else {}
        await c.post("/api/v1/sessions/billing-ab/summary", json=body, headers=BEARER)
        await asyncio.sleep(0.1)
    assert summarizer.calls[0][1] == language


def test_a_pull_request_is_opened_once_however_often_claude_code_links_it():
    link = {"type": "pr-link", "prNumber": 7, "prUrl": "https://github.com/o/r/pull/7"}
    timeline = detail.digest([{**link, "timestamp": at(1)}, {**link, "timestamp": at(2)}])["timeline"]
    assert [(i["kind"], i["at"]) for i in timeline] == [("pr_opened", at(1))]


def test_a_commit_message_given_on_the_lines_after_the_command_is_its_text():
    command = "git commit -q -F - <<'EOF'\nfix: Keep the newest items\n\nBody.\nEOF\ngit log -1"
    merge = "gh pr merge some-branch --rebase"
    timeline = detail.digest([tool("Bash", 1, "b1", command=command), tool("Bash", 2, "b2", command=merge)])["timeline"]
    assert [i["text"] for i in timeline] == ["some-branch", "fix: Keep the newest items"]


def test_a_command_only_mentioned_in_text_is_not_counted():
    written = "cat >> notes.txt <<'EOF'\nmerge = \"gh pr merge 3\" and then git commit\nEOF"
    assert detail.digest([tool("Bash", 1, "b1", command=written)])["timeline"] == []
