"""Every tool has a test file of its own, and the voice can read each one's first line whole."""

import asyncio
import pathlib

from gizmo_mcp.fake import FakeGizmo
from gizmo_mcp.server import FIRST_LINE_MAX, TOOLS, build_server
from gizmo_mcp.service import GizmoService

TESTS = pathlib.Path(__file__).parent


def _tools():
    return {tool.name: tool for tool in asyncio.run(build_server(GizmoService(FakeGizmo())).list_tools())}


def test_every_tool_has_a_test_file_named_after_it():
    """A tool added without one would be tested only by luck."""
    missing = [name for name in TOOLS if not (TESTS / f"test_{name}.py").is_file()]
    assert missing == [], f"write {', '.join(f'tests/test_{name}.py' for name in missing)}"


def test_the_server_offers_exactly_the_tools_it_names():
    assert sorted(_tools()) == sorted(TOOLS)


def test_each_tool_opens_with_a_whole_sentence_the_app_shows_uncut():
    """The Claude app shows a tool by its first line until the tool is loaded, cut at 78 characters."""
    for tool in _tools().values():
        first = tool.description.splitlines()[0]
        assert len(first) <= FIRST_LINE_MAX, tool.name
        assert first.endswith(("?", ".")), tool.name


def test_a_tool_that_only_reads_says_so():
    for tool in _tools().values():
        assert tool.annotations.read_only_hint is True, tool.name
        assert tool.annotations.destructive_hint is False, tool.name
