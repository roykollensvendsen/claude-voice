"""A stand-in for ClaudeSDKClient that replays scripted SDK messages.

It yields the SDK's real message dataclasses, so the bridge is tested against
the same types it will meet from a logged-in Claude Code, without spending quota.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    SystemMessage,
    TextBlock,
    ToolUseBlock,
)


def init(session_id: str) -> SystemMessage:
    return SystemMessage(subtype="init", data={"session_id": session_id})


def say(text: str) -> AssistantMessage:
    return AssistantMessage(content=[TextBlock(text=text)], model="fake")


def use_tool(name: str, tool_input: dict[str, Any], tool_id: str = "tu_1") -> AssistantMessage:
    return AssistantMessage(
        content=[ToolUseBlock(id=tool_id, name=name, input=tool_input)], model="fake"
    )


def result(text: str, session_id: str, is_error: bool = False) -> ResultMessage:
    return ResultMessage(
        subtype="error" if is_error else "success",
        duration_ms=1,
        duration_api_ms=1,
        is_error=is_error,
        num_turns=1,
        session_id=session_id,
        result=text,
        total_cost_usd=0.01,
    )


class Pause:
    """Script step: block the stream until released (or interrupted)."""

    def __init__(self) -> None:
        self.reached = asyncio.Event()
        self.release = asyncio.Event()


class Call:
    """Script step: run a coroutine function with the client's options (e.g. a tool request)."""

    def __init__(self, fn: Callable[[ClaudeAgentOptions], Any]) -> None:
        self.fn = fn


class Boom:
    """Script step: raise from inside the stream."""

    def __init__(self, exc: Exception) -> None:
        self.exc = exc


class FakeClaude:
    """Factory: hands out one FakeClient per turn, each with the next script."""

    def __init__(self, *scripts: list[Any]) -> None:
        self.scripts = list(scripts)
        self.clients: list[FakeClient] = []

    def __call__(self, options: ClaudeAgentOptions) -> FakeClient:
        script = self.scripts.pop(0) if self.scripts else []
        client = FakeClient(options, script)
        self.clients.append(client)
        return client


class FakeClient:
    def __init__(self, options: ClaudeAgentOptions, script: list[Any]) -> None:
        self.options = options
        self.script = script
        self.prompts: list[str] = []
        self.interrupted = asyncio.Event()
        self.connected = False

    async def __aenter__(self) -> FakeClient:
        self.connected = True
        return self

    async def __aexit__(self, *exc: object) -> None:
        self.connected = False

    async def query(self, prompt: str) -> None:
        self.prompts.append(prompt)

    async def interrupt(self) -> None:
        self.interrupted.set()

    async def receive_response(self):
        for step in self.script:
            if self.interrupted.is_set():
                return
            if isinstance(step, Pause):
                step.reached.set()
                waiters = [
                    asyncio.ensure_future(step.release.wait()),
                    asyncio.ensure_future(self.interrupted.wait()),
                ]
                _, pending = await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
                for p in pending:
                    p.cancel()
                continue
            if isinstance(step, Call):
                await step.fn(self.options)
                continue
            if isinstance(step, Boom):
                raise step.exc
            yield step
