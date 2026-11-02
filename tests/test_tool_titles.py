"""Tool titles come from one shared table, so this repository and its forks extend one file."""

import pytest
from mcp.server import MCPServer

from claude_voice import titles


def test_the_shared_table_covers_the_tools_of_both_this_bridge_and_the_kelno_fork():
    table = titles.load()
    for name in ("ask_active_session", "side_question", "approve", "peek_session", "guide", "hand_to_desktop"):
        assert table[name].strip(), name
    assert len(set(table.values())) == len(table), "two tools share a title"


async def test_a_tool_takes_its_title_from_the_table_by_its_name():
    mcp = MCPServer("t")
    tool = titles.titled(mcp, {"ping": "Check the bridge answers"})

    @tool()
    def ping() -> str:
        """Answer pong."""
        return "pong"

    listed = {t.name: t for t in await mcp.list_tools()}
    assert listed["ping"].title == "Check the bridge answers"


def test_a_tool_missing_from_the_table_is_refused_when_it_is_added():
    tool = titles.titled(MCPServer("t"), {})
    with pytest.raises(KeyError, match="no title for tool 'unnamed'"):

        @tool()
        def unnamed() -> str:
            return ""
