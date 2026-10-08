"""A small tool surface: few tools, few parameters, and a project list that fits in a voice."""

import os
import time

import pytest
from claude_agent_sdk import project_key_for_directory
from fakes import FakeClaude
from mcp.client import Client

from claude_voice.server import build_server
from claude_voice.sessions import SessionManager
from claude_voice.store import Store

DAY = 24 * 3600


@pytest.fixture
def world(tmp_path):
    root = tmp_path / "home"
    projects = tmp_path / "projects"
    for d in (root, projects):
        d.mkdir()
    m = SessionManager(Store(tmp_path / "b.db"), project_root=root, client_factory=FakeClaude())
    srv = build_server(m, conversations=lambda **k: [], live_dir=tmp_path / "live", projects_dir=projects)
    return root, projects, srv


def folder(world, name, used_days_ago=None, sessions=1):
    root, projects, _ = world
    (root / name).mkdir()
    if used_days_ago is not None:
        key = projects / project_key_for_directory(str(root / name))
        key.mkdir()
        when = time.time() - used_days_ago * DAY
        for i in range(sessions):
            f = key / f"s{i}.jsonl"
            f.write_text("{}\n")
            os.utime(f, (when, when))


async def projects(srv, **args):
    async with Client(srv) as c:
        return (await c.call_tool("list_projects", args)).structured_content["projects"]


async def test_the_project_list_holds_only_folders_used_lately_newest_first(world):
    folder(world, "billing", used_days_ago=2, sessions=3)
    folder(world, "hydropower", used_days_ago=0)
    folder(world, "old", used_days_ago=60)
    folder(world, "never")
    out = await projects(world[2])
    assert [p["name"] for p in out] == ["hydropower", "billing"]
    assert out[1]["sessions"] == 3 and out[1]["last_used"]


async def test_a_word_finds_any_folder_even_one_never_used(world):
    folder(world, "hydropower", used_days_ago=1)
    folder(world, "hydro-sandbox")
    folder(world, "billing", used_days_ago=1)
    out = await projects(world[2], query="HYDRO")
    assert [p["name"] for p in out] == ["hydropower", "hydro-sandbox"]
    assert out[1]["last_used"] is None and out[1]["sessions"] == 0


async def test_the_project_list_is_never_longer_than_twenty(world):
    for i in range(25):
        folder(world, f"p{i:02}", used_days_ago=1)
    assert len(await projects(world[2])) == 20
    assert len(await projects(world[2], query="p")) == 20


async def test_tools_covered_by_others_are_gone_and_the_rest_take_few_parameters(world):
    async with Client(world[2]) as c:
        tools = {t.name: t for t in (await c.list_tools()).tools}
    assert "recent_activity" not in tools and "get_messages" not in tools
    assert set(tools["list_sessions"].input_schema.get("properties", {})) == set()
    assert set(tools["search_session_history"].input_schema["properties"]) == {"session", "query", "max_chars"}
    assert set(tools["list_projects"].input_schema["properties"]) == {"query"}
    long = [n for n, t in tools.items() if len((t.description or "").split(". ")) > 3]
    assert long == []
