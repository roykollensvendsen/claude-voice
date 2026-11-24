"""One call asks every plugin how it is, at once, with a short limit each (#518).

Once a plugin's status call hung for minutes while the others answered, and finding which one
took a lot of back and forth. Now one slow plugin never holds up the answer.
"""

import asyncio
from types import SimpleNamespace

from mcp.client import Client
from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations
from test_plugins import bridge

from claude_voice import config, plugin_status


class Text:
    def __init__(self, text):
        self.text = text


class FakePlugin:
    """A plugin as the bridge holds it: a name, its allowed tools, and a call."""

    def __init__(self, name, tools, answer=None, wait=0.0, fails=None, is_error=False):
        self.spec = SimpleNamespace(name=name, tools=tools)
        self.answer, self.wait, self.fails, self.is_error = answer, wait, fails, is_error
        self.calls = []
        self.finished = False

    async def call(self, tool, arguments):
        self.calls.append((tool, arguments))
        await asyncio.sleep(self.wait)
        self.finished = True
        if self.fails:
            raise RuntimeError(self.fails)
        structured = self.answer if isinstance(self.answer, dict) else None
        text = self.answer if isinstance(self.answer, str) else ""
        return SimpleNamespace(structured_content=structured, content=[Text(text)], is_error=self.is_error)

    def shown(self, answer):
        return answer


def test_a_plugins_status_tool_is_the_one_named_status():
    assert plugin_status.status_tool(["lamp_on", "lamp_status", "lamp_off"]) == "lamp_status"
    assert plugin_status.status_tool(["status", "other_status"]) == "status"
    assert plugin_status.status_tool(["lamp_on"]) is None


async def test_every_plugin_is_asked_at_once_and_a_slow_one_is_named_without_holding_up_the_answer():
    quick = FakePlugin("lamp", ["lamp_status"], answer={"say": "The lamp is on."})
    slow = FakePlugin("door", ["door_status"], answer={"say": "Shut."}, wait=30)
    started = asyncio.get_running_loop().time()
    out = await plugin_status.check([quick, slow], seconds=0.2)
    assert asyncio.get_running_loop().time() - started < 2
    assert out["answered"] == [{"plugin": "lamp", "state": "The lamp is on."}]
    assert out["too_slow"] == ["door"]
    assert "door" in out["say"] and "1 of 2" in out["say"]


async def test_a_slow_plugins_call_is_left_to_finish_and_never_cut_off():
    slow = FakePlugin("door", ["door_status"], answer={"say": "Shut."}, wait=0.3)
    out = await plugin_status.check([slow], seconds=0.05)
    assert out["too_slow"] == ["door"]
    await asyncio.sleep(0.4)
    assert slow.finished  # never cut off midway
    assert not plugin_status.LEFT_RUNNING  # and let go once it finished


async def test_a_plugin_that_fails_is_named_with_its_reason():
    broken = FakePlugin("lamp", ["lamp_status"], fails="lamp took longer than 30 s")
    refused = FakePlugin("door", ["door_status"], answer="not paired", is_error=True)
    out = await plugin_status.check([broken, refused], seconds=1)
    assert out["failed"] == [
        {"plugin": "lamp", "problem": "lamp took longer than 30 s"},
        {"plugin": "door", "problem": "not paired"},
    ]


async def test_a_plugin_without_a_status_tool_is_named_and_not_called():
    plain = FakePlugin("radio", ["radio_play"])
    out = await plugin_status.check([plain], seconds=1)
    assert out["no_status_tool"] == ["radio"] and plain.calls == []


async def test_each_answer_is_cut_to_one_short_line():
    wordy = FakePlugin("lamp", ["lamp_status"], answer="On.\nBrightness 80 %.\nSince 07.00.")
    long = FakePlugin("door", ["door_status"], answer={"say": "x" * 500})
    plain = FakePlugin("fan", ["fan_status"], answer={"speed": 2, "on": True})
    out = await plugin_status.check([wordy, long, plain], seconds=1)
    states = {a["plugin"]: a["state"] for a in out["answered"]}
    assert states["lamp"] == "On."
    assert len(states["door"]) <= plugin_status.LINE and states["door"].endswith("…")
    assert states["fan"] == '{"speed": 2, "on": true}'


async def test_the_status_tool_is_called_with_no_arguments():
    lamp = FakePlugin("lamp", ["lamp_status"], answer={"say": "On."})
    await plugin_status.check([lamp], seconds=1)
    assert lamp.calls == [("lamp_status", {})]


async def test_no_plugins_says_so():
    assert (await plugin_status.check([], seconds=1))["say"] == "No plugins are running."


LAMP = config.PluginSpec(
    name="lamp", command="lamp-mcp", args=[], cwd="/home/x/lamp", tools=["lamp_status", "lamp_on"], timeout=2.0
)


def lamp():
    server = MCPServer("lamp")

    @server.tool(annotations=ToolAnnotations(read_only_hint=True))
    async def lamp_status() -> dict:
        """How the lamp is."""
        return {"say": "The lamp is on.", "on": True}

    @server.tool()
    async def lamp_on() -> dict:
        """Switch it on."""
        return {"say": "On."}

    return server


async def test_the_plugin_check_asks_the_plugins_that_are_running(tmp_path):
    fake = lamp()
    async with Client(bridge(tmp_path, [LAMP], factory=lambda params: Client(fake))) as c:
        out = (await c.call_tool("plugins_status", {})).structured_content
    assert out["answered"] == [{"plugin": "lamp", "state": "The lamp is on."}]
    assert out["say"] == "1 of 1 plugins answered."
