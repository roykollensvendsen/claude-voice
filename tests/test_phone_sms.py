"""The hub's side of a phone's texts: it passes the phone's answers on as they are.

The phone keeps the rules on what the voice reads out, in its own tool descriptions
(claude-voice-mobile#168 and #181). The hub adds nothing, so these tests check only that a
list of recipients and a run of outcomes come back unchanged, and that no session may confirm.
"""

from __future__ import annotations

import contextlib
import copy
from types import SimpleNamespace
from typing import Any

import pytest

from claude_voice import machines, permissions
from claude_voice.config import MachineSpec

TOOLS = ("agent_info", "sms_send_request", "sms_send_confirm", "sms_send_cancel", "sms_send_outcomes", "sms_since")


class FakePhone:
    """The phone's agent: each tool answers with what `replies` holds for it, and every call is kept."""

    def __init__(self, replies: dict[str, dict[str, Any]]) -> None:
        self.replies = replies
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        self.calls.append((name, copy.deepcopy(arguments)))
        if name == "agent_info":
            return SimpleNamespace(structured_content={"machine": "phone-a", "plugins": []}, content=[], is_error=False)
        return SimpleNamespace(structured_content=copy.deepcopy(self.replies[name]), content=[], is_error=False)


class PhoneMachine(machines.Machine):
    def __init__(self, phone: FakePhone) -> None:
        approval = {"listing": {tool: {} for tool in TOOLS}, "node": "n1"}
        super().__init__(MachineSpec(name="phone-a", address="100.64.0.10"), approval, "key", None, None)
        self.phone = phone

    @contextlib.asynccontextmanager
    async def open(self):
        await self.read_access(self.phone)
        yield self.phone


async def passed_on(reply: dict[str, Any], tool: str, arguments: dict[str, Any]) -> tuple[Any, FakePhone]:
    """What the voice gets back when it calls `tool` on the phone, and the phone that was called."""
    phone = FakePhone({tool: reply})
    machine = PhoneMachine(phone)
    try:
        answer = machine.shown((await machine.call(tool, arguments)).structured_content)
    finally:
        await machine.stop()
    return answer, phone


async def test_a_text_to_five_recipients_is_forwarded_and_its_answer_comes_back_unchanged():
    to = ["Kari", "Ola", "+4700000001", "Mor", "Per"]
    reply = {
        "say": "A text to 5 people. Say yes to send it, or no to stop.",
        "token": "t-9f3a1c",
        "recipients": 5,
        "refused": [{"recipient": 3, "reason": "not a mobile number"}],
    }
    answer, phone = await passed_on(reply, "sms_send_request", {"to": to, "text": "Middag kl 18"})
    assert phone.calls[-1] == ("sms_send_request", {"to": to, "text": "Middag kl 18"})
    assert answer == reply


async def test_sent_then_failed_for_one_recipient_comes_back_unchanged_and_in_order():
    sent = {"id": "p-17", "token": "t-9f3a1c", "recipient": 2, "outcome": "sent", "at": "2026-11-22T14:02:21+01:00"}
    failed = {**sent, "id": "p-18", "outcome": "failed", "at": "2026-11-22T14:02:40+01:00", "reason": "no service"}
    reply = {
        "outcomes": [{**sent, "reason": "handed to the network"}, failed],
        "cursor": "Qm7Tz2pL0aVd",
        "more": False,
        "dropped": 0,
        "reset": False,
    }
    answer, _ = await passed_on(reply, "sms_send_outcomes", {"cursor": "Qm7Tz2pL0aVc"})
    assert answer == reply
    assert [item["outcome"] for item in answer["outcomes"]] == ["sent", "failed"]


@pytest.mark.parametrize("server", ["claude-voice", "claude_ai_claude-voice"])
def test_no_session_may_confirm_a_text_to_a_list_by_any_rule_that_reads_its_arguments(server):
    """Claude Code's rule for an MCP tool names the tool and never its arguments, so a list in
    `to` cannot change who may confirm. Only the deny list names the phone's confirm at all."""
    rules = permissions.load()
    confirm = f"mcp__{server}__phone-a_sms_send_confirm"
    assert confirm in rules.deny
    assert not [rule for rule in rules.ask + rules.allow if "sms_send_confirm" in rule]
