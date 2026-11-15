"""gizmo_status, end to end: through the MCP server, the service and the fake.

This is the walking skeleton: the one tool that runs the whole way from the first commit.
Every new tool gets a file like this one, named after it (tests/test_every_tool_has_a_test.py).
"""

import asyncio

from gizmo_mcp.device import NotSetUpError
from gizmo_mcp.fake import READS_PER_MINUTE, FakeGizmo
from gizmo_mcp.server import build_server
from gizmo_mcp.service import BROKE, NOT_SET_UP, UNREACHABLE, GizmoService


def _call(gizmo, *, simulated=False):
    server = build_server(GizmoService(gizmo), simulated=simulated)
    return asyncio.run(server.call_tool("gizmo_status", {})).structured_content


def test_the_status_says_whether_the_gizmo_is_on_and_its_level():
    answer = _call(FakeGizmo(on=True, level=40))
    assert answer["say"] == "The gizmo is on, at level 40."
    assert (answer["success"], answer["on"], answer["level"], answer["simulated"]) == (True, True, 40, False)
    assert _call(FakeGizmo(on=False, level=0))["say"] == "The gizmo is off, at level 0."


def test_a_simulated_answer_says_so():
    answer = _call(FakeGizmo(), simulated=True)
    assert answer["simulated"] is True
    assert answer["say"].startswith("Simulated: ")


def test_a_gizmo_off_the_network_is_said_with_what_to_check():
    gizmo = FakeGizmo()
    gizmo.unreachable = True
    assert _call(gizmo) == {"success": False, "outcome": "unreachable", "say": UNREACHABLE, "simulated": False}


def test_asking_too_often_says_how_long_to_wait():
    clock = iter(range(100))
    gizmo = FakeGizmo(clock=lambda: float(next(clock)))
    service = GizmoService(gizmo)
    for _ in range(READS_PER_MINUTE):
        assert asyncio.run(service.status())["success"]
    answer = asyncio.run(service.status())
    assert answer["outcome"] == "slow_down"
    assert answer["say"] == "The gizmo is busy; ask again in 54 seconds."


class _Broken:
    async def read(self):
        msg = "a fault nobody planned for"
        raise RuntimeError(msg)


class _NotSetUp:
    async def read(self):
        raise NotSetUpError


def test_every_answer_has_a_sentence_to_say_even_when_the_gizmo_fails():
    """Whatever broke, the voice gets a sentence to read, and never a stack trace."""
    assert _call(_Broken())["say"] == BROKE
    assert _call(_NotSetUp())["say"] == NOT_SET_UP
