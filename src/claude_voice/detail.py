"""What a session's own page shows, read from its transcript: tools, helpers and a timeline.

Read from Claude Code's transcript file, so it costs no model call; the summary on the
same page is made separately, and only when the owner asks for it.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections import Counter
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

TIMELINE = 50  # the newest items kept
TEXT = 120  # characters of each item's text
HELPERS = ("Agent", "Task")
# A command counts only where it starts a line or a step, not inside text a command writes.
STEP = r"(?:^|[;&|(]\s*|\n\s*)"
COMMIT = re.compile(STEP + r"git\b[^|;&\n]*\bcommit\b")
MESSAGE = re.compile(r"""-m\s+(?:"([^"]*)"|'([^']*)')""")
HEREDOC = re.compile(r"<<-?\s*['\"]?(\w+)['\"]?[^\n]*\n(.*?)\n")  # the first line after <<'EOF'
MERGE = re.compile(STEP + r"gh\s+pr\s+merge\b(?:\s+(?!-)(\S+))?")


def read(path: Path) -> list[dict[str, Any]]:
    """Every entry of a transcript, skipping a line still being written."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict):
            out.append(entry)
    return out


def _short(text: str) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= TEXT else text[: TEXT - 1].rstrip() + "…"


def _blocks(entry: dict[str, Any]) -> list[dict[str, Any]]:
    content = (entry.get("message") or {}).get("content")
    return [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []


def _typed(entry: dict[str, Any]) -> str | None:
    """What the owner typed in a user entry, or None for tool results and notices."""
    if entry.get("type") != "user" or entry.get("isMeta") or entry.get("isSidechain"):
        return None
    content = (entry.get("message") or {}).get("content")
    if isinstance(content, list):
        if any(b.get("type") == "tool_result" for b in content if isinstance(b, dict)):
            return None
        content = " ".join(str(b.get("text", "")) for b in content if isinstance(b, dict) and b.get("type") == "text")
    text = str(content or "").strip()
    # Notices Claude Code writes as user turns (task notifications, commands) start with a tag.
    return text if text and not text.startswith("<") else None


def _item(at: Any, kind: str, text: str, **extra: Any) -> dict[str, Any]:
    return {"at": at, "kind": kind, "text": _short(text), **extra}


def _from_command(command: str) -> tuple[str, str] | None:
    """The timeline kind and text a shell command stands for: a merge, a commit, or neither."""
    merged = MERGE.search(command)
    if merged:
        target = (merged.group(1) or "").lstrip("#")
        return "pr_merged", f"#{target}" if target.isdigit() else target or "a pull request"
    commit = COMMIT.search(command)
    if not commit:
        return None
    # The message is read only from the commit itself, never from text before it.
    rest = command[commit.start() :]
    first = rest.split("\n", 1)[0]
    message = MESSAGE.search(first)
    if message:
        return "commit", message.group(1) or message.group(2)
    heredoc = HEREDOC.search(rest)
    if heredoc and heredoc.start() <= len(first) and heredoc.group(2).strip():  # not a later command's
        return "commit", heredoc.group(2).strip()
    return "commit", "a commit"


def digest(entries: list[dict[str, Any]]) -> dict[str, Any]:
    """Tool calls per tool, helpers running and in all, and the newest timeline items."""
    timeline: list[dict[str, Any]] = []
    tools: Counter[str] = Counter()
    helpers: dict[str, str | None] = {}  # the helper call's id -> its background id, if any
    answered: set[str] = set()
    handed_back: set[str] = set()
    linked: set[Any] = set()
    for entry in entries:
        if entry.get("isSidechain"):
            continue
        at = entry.get("timestamp")
        kind = entry.get("type")
        typed = _typed(entry)
        if typed:
            timeline.append(_item(at, "prompt", typed))
        # Claude Code links the same pull request again and again; it was opened once.
        if kind == "pr-link" and (entry.get("prUrl") or entry.get("prNumber")) not in linked:
            linked.add(entry.get("prUrl") or entry.get("prNumber"))
            number = entry.get("prNumber")
            timeline.append(_item(at, "pr_opened", f"#{number}" if number else "pull request", url=entry.get("prUrl")))
        if kind == "system" and entry.get("subtype") == "compact_boundary":
            timeline.append(_item(at, "compact", "The conversation was compacted"))
        origin = entry.get("origin") or {}
        if isinstance(origin, dict) and origin.get("senderTaskId"):
            handed_back.add(str(origin["senderTaskId"]))
        if kind == "queue-operation":
            handed_back.update(re.findall(r"<task-id>([^<]+)</task-id>", str(entry.get("content", ""))))
        # Claude Code also keeps copies of turns (such as api-request-blob); only the turns count.
        for block in _blocks(entry) if kind in ("user", "assistant") else []:
            if block.get("type") == "tool_use":
                name, given = str(block.get("name", "")), block.get("input") or {}
                tools[name] += 1
                if name in HELPERS:
                    helpers[str(block.get("id"))] = None
                elif name == "AskUserQuestion":
                    first = (given.get("questions") or [{}])[0]
                    timeline.append(_item(at, "question", str(first.get("question", "")) or "a question"))
                elif name == "Bash":
                    found = _from_command(str(given.get("command", "")))
                    if found:
                        timeline.append(_item(at, *found))
            elif block.get("type") == "tool_result":
                tool_id = str(block.get("tool_use_id"))
                answered.add(tool_id)
                if tool_id in helpers:
                    launched = entry.get("toolUseResult") or {}
                    if isinstance(launched, dict) and launched.get("status") == "async_launched":
                        helpers[tool_id] = str(launched.get("agentId"))
                if block.get("is_error"):
                    content = block.get("content")
                    if isinstance(content, list):
                        content = " ".join(str(c.get("text", "")) for c in content if isinstance(c, dict))
                    timeline.append(_item(at, "error", str(content or "a tool failed").strip().splitlines()[0]))
    # RULE: a helper counts as running until it has handed back
    running = sum(
        1
        for call, background in helpers.items()
        if (background is None and call not in answered) or (background is not None and background not in handed_back)
    )
    return {
        "tool_calls": {"total": sum(tools.values()), "by_tool": dict(tools.most_common())},
        "subagents": {"running": running, "total": len(helpers)},
        "timeline": list(reversed(timeline))[:TIMELINE],
    }


class Summaries:
    """A summary per session, made by a small model only on request and kept until the next.

    A request while one is being made joins it, so a double tap costs one call; the last
    good summary survives a failure and a restart of the bridge.
    """

    def __init__(
        self,
        store: Any,
        summarize: Callable[[str, str | None, str | None], Awaitable[str]],
        publish: Callable[[str, dict[str, Any]], None],
    ) -> None:
        self.store, self.summarize, self.publish = store, summarize, publish
        self.running: dict[str, asyncio.Task[str | None]] = {}
        self.errors: dict[str, str] = {}

    def state(self, sid: str) -> dict[str, Any]:
        kept = self.store.get_setting(f"summary:{sid}") or {}
        if sid in self.running and not self.running[sid].done():
            state = "generating"
        elif sid in self.errors:
            state = "error"
        else:
            state = "ready" if kept.get("summary") else "none"
        return {
            "summary": kept.get("summary"),
            "summary_state": state,
            "summary_error": self.errors.get(sid),
            "summary_at": kept.get("at"),
        }

    def start(self, sid: str, text: str, language: str | None) -> asyncio.Task[str | None]:
        """The summary being made for `sid`: the one already running, or a new one."""
        # RULE: a second request for a summary joins the one already running
        current = self.running.get(sid)
        if current is not None and not current.done():
            return current
        self.errors.pop(sid, None)
        task = asyncio.ensure_future(self._make(sid, text, language))
        self.running[sid] = task
        return task

    async def _make(self, sid: str, text: str, language: str | None) -> str | None:
        try:
            summary = (await self.summarize(text, None, language)).strip()
        except Exception as exc:
            self.errors[sid] = str(exc)[:300] or type(exc).__name__
            self.publish("summary", {"session": sid, "summary": None, "summary_state": "error"})
            return None
        at = datetime.now(UTC).isoformat(timespec="seconds")
        self.store.set_setting(f"summary:{sid}", {"summary": summary, "at": at})
        self.publish("summary", {"session": sid, "summary": summary, "summary_state": "ready"})
        return summary
