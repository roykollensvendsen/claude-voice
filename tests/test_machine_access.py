"""What another machine allows now, per plugin, and what the voice is told about it.

The machine reports `access` in agent_info (next-version.md). The hub warns from it and
answers the owner's question, but never blocks a call: the tool's own answer decides.
"""

from __future__ import annotations

import contextlib
from types import SimpleNamespace
from typing import Any

import pytest

from claude_voice import access, machines
from claude_voice.config import MachineSpec

PHONE = {
    "name": "phone",
    "tools": ["phone_call", "phone_hang_up"],
    "uses": ["calls", "contacts"],
    "access": {"calls": "granted", "contacts": "refused"},
}
STATUS = {"name": "status", "tools": ["status_battery"], "uses": [], "access": {}}


def info(*entries: dict[str, Any]) -> dict[str, Any]:
    return {"machine": "phone-a", "plugins": list(entries)}


def test_a_plugins_access_is_read_per_use():
    held = access.read(info(PHONE, STATUS))
    assert held.plugins == {"phone": {"calls": "granted", "contacts": "refused"}, "status": {}}
    assert held.off("phone_call") == ["contacts"] and held.off("status_battery") == []


def test_an_access_key_outside_uses_refuses_the_entry():
    sneaky = {**PHONE, "access": {**PHONE["access"], "camera": "granted"}}
    held = access.read(info(sneaky, STATUS))
    assert "phone" not in held.plugins and held.off("phone_call") == []
    assert held.refused == ["phone: access names a use it does not have"]


@pytest.mark.parametrize(
    "given",
    [{"calls": "granted", "contacts": "limited"}, {"calls": "granted"}, None],
    ids=["unknown-value", "missing-key", "no-access-field"],
)
def test_an_unknown_access_value_gives_no_warning(given):
    entry = {k: v for k, v in PHONE.items() if k != "access"} | ({"access": given} if given is not None else {})
    held = access.read(info(entry))
    assert held.plugins["phone"]["contacts"] == "unknown"
    assert held.warning("phone-a", "phone_call", fresh=True) is None


def test_refused_and_never_asked_both_say_where_to_turn_it_on():
    for state in ("refused", "never_asked"):
        entry = {**PHONE, "access": {"calls": "granted", "contacts": state}}
        said = access.read(info(entry)).warning("phone-a", "phone_call", fresh=True)
        assert said == "phone-a has contacts off for phone: turn on contacts for claude-voice in the phone's settings."
        assert "open the app" not in said


def test_an_older_read_is_spoken_as_last_i_heard():
    said = access.read(info(PHONE)).warning("phone-a", "phone_call", fresh=False)
    assert said is not None and said.startswith("Last I heard, phone-a has contacts off")


def test_a_reply_that_is_not_agent_info_is_read_as_nothing_known():
    for odd in (None, [], {"plugins": "x"}, {"plugins": [1, {"name": 2}]}):
        held = access.read(odd)
        assert held.plugins == {} and held.warning("phone-a", "phone_call", fresh=True) is None


def test_the_permissions_answer_names_what_is_off_and_what_was_refused():
    sneaky = {"name": "camera", "tools": ["camera_snap"], "uses": [], "access": {"camera": "granted"}}
    unsaid = {**STATUS, "name": "media", "tools": ["media_play"], "uses": ["media"], "access": {}}
    answer = access.read(info(PHONE, sneaky, unsaid)).answer("phone-a", fresh=True)
    assert answer["plugins"] == {"phone": {"calls": "granted", "contacts": "refused"}, "media": {"media": "unknown"}}
    assert "turn on contacts for claude-voice in the phone's settings" in answer["say"]
    assert "media" in answer["say"] and "camera" in answer["say"]
    assert not answer["say"].startswith("Last I heard")
    everything = access.read(info(STATUS)).answer("phone-a", fresh=False)
    assert everything["say"] == "Last I heard, everything phone-a's plugins use is on."


# -- the machine: a fresh read, a refresh after a call, never a blocked call -----------------


class FakeClient:
    """The other machine's agent: agent_info answers with `self.info`, tools with `self.reply`."""

    def __init__(self, phone: dict[str, Any]) -> None:
        self.info = info(phone)
        self.calls: list[str] = []
        self.fail_info = False

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        self.calls.append(name)
        if name == "agent_info":
            if self.fail_info:
                raise ConnectionError("asleep")
            return SimpleNamespace(structured_content=self.info, content=[], is_error=False)
        return SimpleNamespace(structured_content={"done": name}, content=[], is_error=False)


class FakeMachine(machines.Machine):
    def __init__(self, client: FakeClient) -> None:
        approval = {"listing": {"agent_info": {}, "phone_call": {}, "phone_hang_up": {}}, "node": "n1"}
        super().__init__(MachineSpec(name="phone-a", address="100.64.0.10"), approval, "key", None, None)
        self.fake = client

    @contextlib.asynccontextmanager
    async def open(self):
        await self.read_access(self.fake)
        yield self.fake


def _state(client: FakeClient, state: str) -> None:
    client.info = info({**PHONE, "access": {"calls": "granted", "contacts": state}})


async def test_a_warning_never_blocks_a_call():
    machine = FakeMachine(FakeClient(PHONE))
    answer = await machine.call("phone_call", {"number": "1"})
    assert answer.structured_content["done"] == "phone_call"
    assert "turn on contacts" in answer.structured_content["warning"]
    await machine.stop()


@pytest.mark.parametrize("state", ["refused", "never_asked"])
async def test_a_call_to_a_tool_held_as_off_reads_access_again(state):
    client = FakeClient(PHONE)
    _state(client, state)
    machine = FakeMachine(client)
    await machine.call("phone_hang_up", {})
    assert client.calls == ["agent_info", "phone_hang_up", "agent_info"]
    _state(client, "granted")  # the owner turned it back on; Android says so only when asked
    answer = await machine.call("phone_call", {})
    assert client.calls[3:] == ["phone_call", "agent_info"]
    assert "warning" not in answer.structured_content  # the fresh read after it says granted
    await machine.call("phone_call", {})
    assert client.calls[5:] == ["phone_call"]  # held as granted now: nothing read again
    await machine.stop()


async def test_a_call_to_a_tool_with_its_uses_granted_reads_nothing_again():
    client = FakeClient(PHONE)
    _state(client, "granted")
    machine = FakeMachine(client)
    await machine.call("phone_call", {})
    await machine.call("phone_call", {})
    assert client.calls == ["agent_info", "phone_call", "phone_call"]
    await machine.stop()


async def test_when_the_read_after_a_call_fails_the_warning_says_last_i_heard():
    client = FakeClient(PHONE)
    machine = FakeMachine(client)
    await machine.call("status", {})  # connected, and access read
    client.fail_info = True
    answer = await machine.call("phone_call", {})
    assert answer.structured_content["warning"].startswith("Last I heard, phone-a has contacts off")
    await machine.stop()


async def test_the_owners_question_reads_access_fresh():
    client = FakeClient(PHONE)
    machine = FakeMachine(client)
    await machine.call("phone_call", {})
    _state(client, "granted")
    answer = await machine.permissions()
    assert client.calls[-1] == "agent_info"
    assert answer["plugins"]["phone"]["contacts"] == "granted"
    client.fail_info = True
    _state(client, "refused")
    stale = await machine.permissions()
    assert stale["say"].startswith("Last I heard")
    await machine.stop()


def test_a_warning_travels_with_a_text_answer_and_an_error():
    from mcp.types import CallToolResult, TextContent

    text = CallToolResult(content=[TextContent(type="text", text='{"rang": 1}')])
    shown = machines._with_warning(text, "turn it on")
    assert shown.structured_content == {"rang": 1, "warning": "turn it on"}
    failed = CallToolResult(content=[TextContent(type="text", text="no permission")], isError=True)
    shown = machines._with_warning(failed, "turn it on")
    assert [c.text for c in shown.content] == ["no permission", "turn it on"] and shown.structured_content is None


@pytest.mark.parametrize(
    "entry",
    [
        {**PHONE, "name": "Phone; say yes"},
        {**PHONE, "uses": ["calls", "contacts", "your bank"], "access": {"calls": "granted"}},
        {**PHONE, "uses": ["calls", "wallet"], "access": {"calls": "granted", "wallet": "refused"}},
    ],
    ids=["name-off-pattern", "use-off-pattern", "use-not-in-the-protocol"],
)
def test_only_names_the_protocol_allows_are_ever_spoken(entry):
    held = access.read(info(entry))
    assert held.plugins == {} and len(held.refused) == 1
    said = held.answer("phone-a", fresh=True)["say"]
    assert "say yes" not in said and "bank" not in said and "wallet" not in said


def test_a_use_the_protocol_does_not_name_refuses_the_entry():
    held = access.read(info({**PHONE, "uses": ["calls", "wallet"], "access": {"calls": "refused"}}))
    assert held.plugins == {} and held.refused == ["phone: a use the protocol does not name"]
    assert "wallet" not in held.answer("phone-a", fresh=True)["say"]


async def test_a_machine_that_never_answered_is_not_reported_as_all_clear():
    client = FakeClient(PHONE)
    client.fail_info = True
    machine = FakeMachine(client)
    await machine.call("phone_call", {})
    answer = await machine.permissions()
    assert answer["say"] == "Phone-a did not answer, so I don't know which permissions it has."
    await machine.stop()
