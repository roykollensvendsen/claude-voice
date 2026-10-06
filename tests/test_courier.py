"""The courier that carries a message into a running session, kept warm between deliveries."""

import asyncio
import json

from claude_agent_sdk import (
    AssistantMessage,
    PermissionResultAllow,
    PermissionResultDeny,
    ResultMessage,
    ToolPermissionContext,
    ToolUseBlock,
)

from claude_voice.transcripts import Courier


def sent(to):
    return AssistantMessage(content=[ToolUseBlock(id="t", name="SendMessage", input={"to": to})], model="fake")


def done(text):
    return ResultMessage(
        subtype="success", duration_ms=1, duration_api_ms=1, is_error=False, num_turns=1, session_id="c", result=text
    )


class FakeCourierClient:
    """One client that answers each query with the next scripted reply."""

    def __init__(self, options, replies):
        self.options = options
        self.replies = replies
        self.prompts = []
        self.closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        self.closed = True

    async def query(self, prompt):
        self.prompts.append(prompt)

    async def receive_response(self):
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        for message in reply:
            await asyncio.sleep(0)
            yield message


class Factory:
    def __init__(self, *per_client):
        self.per_client = list(per_client)
        self.clients = []

    def __call__(self, options):
        client = FakeCourierClient(options, self.per_client.pop(0))
        self.clients.append(client)
        return client


OK = [sent("billing"), done("DELIVERED: queued")]


async def test_one_warm_client_carries_many_messages():
    factory = Factory([OK, OK, OK])
    courier = Courier(client_factory=factory)
    results = [await courier.deliver("billing", f"m{i}") for i in range(3)]
    assert all(r["delivered"] for r in results)
    assert len(factory.clients) == 1
    assert "m2" in factory.clients[0].prompts[2]
    await courier.close()
    assert factory.clients[0].closed


async def test_the_courier_is_fast_cheap_and_can_only_send_messages():
    factory = Factory([OK])
    courier = Courier(client_factory=factory)
    await courier.deliver("billing", "hi")
    opts = factory.clients[0].options
    assert opts.model == "haiku"
    assert json.loads(opts.settings) == {"disableClaudeAiConnectors": True}
    assert "claude-voice-msg-" in str(opts.cwd)
    ask = opts.can_use_tool
    ctx = ToolPermissionContext()
    assert isinstance(await ask("SendMessage", {}, ctx), PermissionResultAllow)
    assert isinstance(await ask("Bash", {"command": "ls"}, ctx), PermissionResultDeny)
    await courier.close()


async def test_a_message_without_a_send_is_not_delivered():
    factory = Factory([[done("DELIVERED: trust me")]])
    courier = Courier(client_factory=factory)
    assert (await courier.deliver("billing", "hi"))["delivered"] is False
    await courier.close()


async def test_a_broken_client_is_replaced_and_the_message_retried():
    factory = Factory([RuntimeError("cli died")], [OK])
    courier = Courier(client_factory=factory)
    assert (await courier.deliver("billing", "hi"))["delivered"] is True
    assert len(factory.clients) == 2 and factory.clients[0].closed
    await courier.close()


async def test_the_courier_starts_afresh_after_a_number_of_messages():
    factory = Factory([OK, OK], [OK])
    courier = Courier(client_factory=factory, fresh_after=2)
    for i in range(3):
        await courier.deliver("billing", f"m{i}")
    assert len(factory.clients) == 2 and factory.clients[0].closed
    await courier.close()


async def test_simultaneous_messages_are_carried_one_at_a_time():
    factory = Factory([OK, OK])
    courier = Courier(client_factory=factory)
    results = await asyncio.gather(courier.deliver("billing", "a"), courier.deliver("billing", "b"))
    assert all(r["delivered"] for r in results)
    assert len(factory.clients[0].prompts) == 2
    await courier.close()
