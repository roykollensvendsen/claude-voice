"""Rule 4a of the plugin standard: an answer may name a read-only look for the voice to take next."""

from mcp.client import Client
from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations
from test_plugins import bridge

from claude_voice import config

GARAGE = config.PluginSpec(
    name="garage",
    command="garage-door-mcp",
    args=["--simulated"],
    cwd="/home/x/garage-door",
    tools=["garage_status", "garage_confirm"],
    timeout=2.0,
)
LOOKS = ToolAnnotations(read_only_hint=True)


def stand_in(follow):
    """A garage whose status answer carries `follow` as its next, and whose confirm presses."""
    server = MCPServer("garage")
    pressed = []

    @server.tool(annotations=LOOKS)
    async def garage_status() -> dict:
        """Where the door is."""
        answer = {"say": "The door is opening.", "door": "opening"}
        if follow is not None:
            answer["next"] = follow
        return answer

    @server.tool()
    async def garage_confirm(token: str = "") -> dict:
        """Press the button."""
        pressed.append(token)
        return {"say": "Pressed."}

    return server, pressed


class Strict(Client):
    """A plugin connection that keeps each call's arguments, as a plugin whose schema has
    additionalProperties false would refuse anything it did not name."""

    seen: list[dict] = []

    async def call_tool(self, name, arguments=None, *args, **kwargs):
        Strict.seen.append(dict(arguments or {}))
        return await super().call_tool(name, arguments, *args, **kwargs)


def factory(fake):
    return lambda params: Strict(fake)


async def look(tmp_path, follow, arguments=None):
    fake, pressed = stand_in(follow)
    Strict.seen = []
    async with Client(bridge(tmp_path, [GARAGE], factory=factory(fake))) as c:
        out = (await c.call_tool("garage_status", arguments or {})).structured_content
    return out, pressed


async def test_a_next_naming_a_read_only_tool_of_the_same_plugin_is_passed_on(tmp_path):
    out, _ = await look(tmp_path, {"tool": "garage_status", "args": {}})
    assert out["next"] == {"tool": "garage_status", "args": {"_hop": 1}}
    assert out["say"] == "The door is opening."


async def test_a_next_naming_a_tool_that_presses_is_refused(tmp_path):
    out, pressed = await look(tmp_path, {"tool": "garage_confirm", "args": {"token": "t1"}})
    assert "next" not in out and pressed == []
    assert out["say"] == "The door is opening."  # the answer itself still reaches the voice


async def test_a_next_naming_an_unknown_tool_is_refused(tmp_path):
    for name in ("garage_arm", "tv_status", "", None):
        out, _ = await look(tmp_path / str(name), {"tool": name, "args": {}})
        assert "next" not in out, name


async def test_a_next_that_is_not_a_tool_and_its_args_is_refused(tmp_path):
    for follow in ("garage_status", ["garage_status"], {"tool": "garage_status", "args": "x"}):
        out, _ = await look(tmp_path / str(len(str(follow))), follow)
        assert "next" not in out, follow


async def test_the_plugin_never_sees_the_bridges_hop_count(tmp_path):
    out, _ = await look(tmp_path, {"tool": "garage_status", "args": {}}, {"_hop": 2})
    assert Strict.seen == [{}]
    assert out["next"]["args"] == {"_hop": 3}


async def test_the_bridge_stops_a_chain_after_six_calls(tmp_path):
    fifth, _ = await look(tmp_path / "a", {"tool": "garage_status", "args": {}}, {"_hop": 4})
    sixth, _ = await look(tmp_path / "b", {"tool": "garage_status", "args": {}}, {"_hop": 5})
    assert fifth["next"]["args"] == {"_hop": 5}
    assert "next" not in sixth and sixth["say"] == "The door is opening."


async def test_a_hop_count_that_is_not_a_small_whole_number_ends_the_chain(tmp_path):
    for hop in ("1", -1, 2.5, True, 10**9):
        out, _ = await look(tmp_path / str(hop), {"tool": "garage_status", "args": {}}, {"_hop": hop})
        assert "next" not in out, hop
        assert Strict.seen == [{}], hop


async def test_only_a_read_only_tool_offers_the_bridges_hop_count(tmp_path):
    fake, _ = stand_in(None)
    async with Client(bridge(tmp_path, [GARAGE], factory=factory(fake))) as c:
        schemas = {t.name: t.input_schema for t in (await c.list_tools()).tools}
    hop = schemas["garage_status"]["properties"]["_hop"]
    assert hop["type"] == "integer" and (hop["minimum"], hop["maximum"]) == (0, 5)
    assert "pass it back unchanged" in hop["description"]
    assert "_hop" not in schemas["garage_status"].get("required", [])
    assert "_hop" not in schemas["garage_confirm"]["properties"]
