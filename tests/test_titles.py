"""Every tool has a title: the short, plain name a client can show, for example in an approval prompt."""

from fakes import FakeClaude
from mcp.client import Client

from claude_voice import metrics
from claude_voice.server import build_server
from claude_voice.sessions import SessionManager
from claude_voice.store import Store


async def test_every_tool_has_its_own_title(tmp_path):
    (tmp_path / "src").mkdir()
    m = SessionManager(Store(tmp_path / "b.db"), project_root=tmp_path / "src", client_factory=FakeClaude())
    async with Client(build_server(m)) as c:
        tools = (await c.list_tools()).tools
    untitled = [t.name for t in tools if not (t.title or "").strip()]
    assert not untitled, untitled
    titles = [t.title for t in tools]
    assert len(set(titles)) == len(titles), "two tools share a title"


async def test_the_server_gives_its_title_and_version(tmp_path):
    (tmp_path / "src").mkdir()
    m = SessionManager(Store(tmp_path / "b.db"), project_root=tmp_path / "src", client_factory=FakeClaude())
    async with Client(build_server(m)) as c:
        info = c.server_info
    assert info.title == "Claude voice bridge"
    assert info.version == metrics.version()
