"""Read and reach Claude Code sessions that are open elsewhere, e.g. in a terminal.

Reading uses Claude Code's own transcript on disk. Delivering a message uses a
short helper session whose only permitted tools are the official cross-session
messaging ones (ListAgents, SendMessage), so the text lands in the live session
itself rather than in a copy of its conversation.
"""

from __future__ import annotations

import re
import tempfile
from collections.abc import Iterable
from typing import Any

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    PermissionResultAllow,
    PermissionResultDeny,
    ResultMessage,
    ToolUseBlock,
    get_session_messages,
)

MESSAGING_TOOLS = frozenset({"ListAgents", "SendMessage", "ToolSearch"})


def recent_turns(messages: Iterable[Any], limit: int | None = 10) -> list[dict[str, Any]]:
    """What a person would read: prompts and Claude's text, with tool names folded in."""
    turns: list[dict[str, Any]] = []
    pending_tools: list[str] = []
    for m in messages:
        content = m.message.get("content")
        if m.type == "user":
            texts = (
                [content]
                if isinstance(content, str)
                else [b.get("text", "") for b in content or [] if b.get("type") == "text"]
            )
            text = "\n".join(t for t in texts if t.strip())
            if text:
                turns.append({"role": "user", "text": text})
        elif m.type == "assistant" and isinstance(content, list):
            for b in content:
                if b.get("type") == "tool_use":
                    pending_tools.append(b.get("name", "?"))
                elif b.get("type") == "text" and b.get("text", "").strip():
                    turns.append({"role": "assistant", "text": b["text"], "tools": pending_tools})
                    pending_tools = []
    return turns if limit is None else turns[-limit:]


def search_turns(
    turns: list[dict[str, Any]], query: str, limit: int = 5, context_chars: int = 200
) -> list[dict[str, Any]]:
    """Turns matching the query's words, most relevant first, each cut to a snippet.

    Relevance: how many distinct query words a turn contains, then how often
    they occur; ties go to the newer turn. `turn` is the index in `turns`.
    """
    terms = {w for w in re.findall(r"\w+", query.lower()) if len(w) > 1}
    patterns = {w: re.compile(rf"\b{re.escape(w)}", re.IGNORECASE) for w in terms}
    scored = []
    for i, turn in enumerate(turns):
        counts = {w: len(p.findall(turn["text"])) for w, p in patterns.items()}
        present = [w for w, n in counts.items() if n]
        if present:
            scored.append((len(present), sum(counts.values()), i))
    scored.sort(reverse=True)
    out = []
    for _, _, i in scored[:limit]:
        turn = turns[i]
        out.append(
            {
                "turn": i,
                "role": turn["role"],
                "text": _snippet(turn["text"], list(patterns.values()), context_chars),
            }
        )
    return out


def _snippet(text: str, patterns: list[re.Pattern], context: int) -> str:
    found = [m for p in patterns if (m := p.search(text))]
    first = min(found, key=lambda m: m.start(), default=None)
    pos, length = (first.start(), len(first.group())) if first else (0, 0)
    start, end = max(0, pos - context), min(len(text), pos + length + context)
    return ("…" if start else "") + text[start:end].strip() + ("…" if end < len(text) else "")


def read_transcript(session_id: str, directory: str | None) -> list[Any]:
    return get_session_messages(session_id, directory=directory)


async def deliver(name: str, text: str) -> dict[str, Any]:
    """Send `text` into the running session called `name` via SendMessage."""

    async def only_messaging(tool, tool_input, context):
        if tool in MESSAGING_TOOLS:
            return PermissionResultAllow()
        return PermissionResultDeny(message="This helper may only send messages.")

    sent = False
    detail = ""
    with tempfile.TemporaryDirectory(prefix="claude-voice-msg-") as cwd:
        opts = ClaudeAgentOptions(
            cwd=cwd,
            can_use_tool=only_messaging,
            setting_sources=[],
            allowed_tools=[],
            max_turns=6,
            system_prompt=(
                "You are a message courier. Deliver the user's message verbatim with "
                "SendMessage to the named session (use ListAgents if unsure of the name). "
                "Do nothing else. Reply in one line: DELIVERED or FAILED plus the reason."
            ),
        )
        async with ClaudeSDKClient(opts) as client:
            await client.query(f"Session name: {name}\nMessage:\n{text}")
            async for m in client.receive_response():
                if isinstance(m, AssistantMessage):
                    sent = sent or any(
                        isinstance(b, ToolUseBlock) and b.name == "SendMessage" for b in m.content
                    )
                elif isinstance(m, ResultMessage):
                    detail = (m.result or m.subtype or "").strip()
    delivered = sent and detail.upper().startswith("DELIVERED")
    return {"delivered": delivered, "detail": detail}
