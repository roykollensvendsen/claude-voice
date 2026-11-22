"""One fleet across machines: the hub's session tools see and reach the sessions on every
approved machine, named with the machine in front, and never wait long for one."""

import time

import httpx
from test_agent_sessions import laptop_bridge
from test_machines import SPEC, Laptop, approve, call
from test_plugins import bridge_with

from claude_voice import machines


async def hub_with_laptop(tmp_path, http=None):
    bridge, deliver = laptop_bridge(tmp_path / "laptop")
    laptop = Laptop(tmp_path / "laptop", bridge=bridge)
    return laptop, deliver


def unstamped(sent):
    """What arrived on the other machine, without the origin line that machine stamps on it."""
    return [(name, text.split("\n", 1)[1]) for name, text in sent]


def the_hub(tmp_path, laptop, http=None):
    return bridge_with(
        tmp_path / "hub-home",
        machines=[SPEC],
        machine_folder=tmp_path / "hub",
        http=http or laptop.http,
        whois=laptop.whois,
    )


async def test_the_sessions_on_every_machine_are_in_one_list(tmp_path):
    laptop, _ = await hub_with_laptop(tmp_path)
    async with laptop:
        await approve(laptop, tmp_path / "hub")
        hub = the_hub(tmp_path, laptop)
        listed = (await call(hub, "list_active_sessions")).structured_content
        recap = (await call(hub, "fleet_recap")).structured_content
    assert {"name": "laptop-a/mod", "machine": "laptop-a"}.items() <= listed["sessions"][0].items()
    assert [s["name"] for s in recap["sessions"]] == ["laptop-a/mod"]
    assert listed["machines"][0]["name"] == "laptop-a" and listed["machines"][0]["reachable"] is True


async def test_a_session_on_another_machine_is_reached_by_the_machine_name_in_front(tmp_path):
    laptop, deliver = await hub_with_laptop(tmp_path)
    async with laptop:
        await approve(laptop, tmp_path / "hub")
        hub = the_hub(tmp_path, laptop)
        sent = await call(hub, "message_active_session", session="laptop-a/mod", message="hei")
        read = await call(hub, "read_session_output", session="laptop-a/mod")
    assert not sent.is_error and unstamped(deliver.sent) == [("mod", "hei")]
    assert not read.is_error and read.structured_content["session"] == "laptop-a/mod"


async def test_a_machine_that_does_not_answer_is_reported_with_its_state(tmp_path):
    laptop, _ = await hub_with_laptop(tmp_path)

    async def silent(request):
        import asyncio

        await asyncio.sleep(30)

    def asleep(spec, headers):
        return httpx.AsyncClient(transport=httpx.MockTransport(silent), base_url=machines.url(spec), headers=headers)

    async with laptop:
        await approve(laptop, tmp_path / "hub")
    hub = the_hub(tmp_path, laptop, http=asleep)
    started = time.monotonic()
    listed = (await call(hub, "list_active_sessions")).structured_content
    health = (await call(hub, "health")).structured_content
    assert time.monotonic() - started < 6
    assert listed["sessions"] == []
    gone = listed["machines"][0]
    assert gone["reachable"] is False and gone["error"] == "did not answer" and gone["state"] in ("offline", "slow")
    assert health["machines"][0]["name"] == "laptop-a"


class Hanging:
    """A machine that connected, then never answers."""

    def __init__(self, name):
        from claude_voice.config import MachineSpec

        self.machine = MachineSpec(name=name, address="100.64.0.2")
        self.approval, self.last_failure = {"seen_as": name}, None
        self.spec = type("Spec", (), {"tools": ["list_active_sessions"]})()

    async def call(self, tool, arguments):
        import asyncio

        await asyncio.sleep(30)


async def test_looking_across_machines_never_waits_long_for_one():
    from claude_voice import fleet

    across = fleet.Fleet()
    across.machines = [Hanging("a"), Hanging("b")]
    started = time.monotonic()
    await across.refresh()
    assert time.monotonic() - started < fleet.ASK_SECONDS + 1  # both asked at once, each cut off
    assert [m["error"] for m in across.rows()] == ["did not answer", "did not answer"]


async def test_a_session_on_another_machine_gets_its_number_from_the_same_pool_as_the_hub(tmp_path):
    laptop, deliver = await hub_with_laptop(tmp_path)
    async with laptop:
        await approve(laptop, tmp_path / "hub")
        hub = the_hub(tmp_path, laptop)
        listed = (await call(hub, "list_active_sessions")).structured_content["sessions"]
        again = (await call(hub, "list_active_sessions")).structured_content["sessions"]
        there = next(s for s in listed if s["name"] == "laptop-a/mod")
        sent = await call(hub, "message_active_session", session=f"nummer {there['number']}", message="hei")
    assert isinstance(there["number"], int)
    assert [s["number"] for s in again] == [s["number"] for s in listed]
    assert not sent.is_error and unstamped(deliver.sent) == [("mod", "hei")]


def _numbers_in(body):
    """Every `number` anywhere in an answer."""
    if isinstance(body, dict):
        return [v for k, v in body.items() if k == "number"] + [n for v in body.values() for n in _numbers_in(v)]
    if isinstance(body, list):
        return [n for v in body for n in _numbers_in(v)]
    return []


async def test_a_session_number_from_another_machine_is_never_passed_on(tmp_path):
    laptop, _ = await hub_with_laptop(tmp_path)
    async with laptop:
        await approve(laptop, tmp_path / "hub")
        hub = the_hub(tmp_path, laptop)
        listed = (await call(hub, "laptop-a_list_active_sessions")).structured_content
        tree = (await call(hub, "laptop-a_session_tree")).structured_content
        recap = (await call(hub, "laptop-a_fleet_recap")).structured_content
        page = (await call(hub, "get_active_session", session="laptop-a/mod")).structured_content
    assert listed["sessions"] and tree["nodes"] and recap["sessions"]
    # The laptop numbers its sessions from its own pool, so its "1" may be the hub's own #1.
    assert _numbers_in([listed, tree, recap, page]) == []


async def test_a_session_on_another_machine_is_made_the_active_one_by_its_number(tmp_path):
    laptop, _ = await hub_with_laptop(tmp_path)
    async with laptop:
        await approve(laptop, tmp_path / "hub")
        hub = the_hub(tmp_path, laptop)
        listed = (await call(hub, "list_active_sessions")).structured_content["sessions"]
        there = next(s for s in listed if s["name"] == "laptop-a/mod")
        made = await call(hub, "set_active_session", session=f"nummer {there['number']}")
    assert not made.is_error, made.content
    assert made.structured_content["name"] == "laptop-a/mod"
