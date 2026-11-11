"""What a session's own page shows, read from its transcript: tools, helpers and a timeline.

Read from Claude Code's transcript file, so it costs no model call; the summary on the
same page is made separately, and only when the owner asks for it.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections import Counter
from collections.abc import Awaitable, Callable, Iterable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

TIMELINE = 50  # the newest items kept
TEXT = 120  # characters of each item's text
HELPERS = ("Agent", "Task")
RETRY = "Try again, and if it fails again run `claude-voice check`, which says what is missing."
# A command counts only where it starts a line or a step, not inside text a command writes.
STEP = r"(?:^|[;&|(]\s*|\n\s*)"
COMMIT = re.compile(STEP + r"git\s[^|;&\n]*\bcommit\b")  # git and a space: not git(...) in a program
MESSAGE = re.compile(r"""(?:^|\s)-[a-zA-Z]*m\s+(?:"([^"]*)"|'([^']*)')""")  # -m, or combined as in -qam
HEREDOC = re.compile(r"<<-?\s*['\"]?(\w+)['\"]?[^\n]*\n(.*?)\n")  # the first line after <<'EOF'
MERGE = re.compile(STEP + r"gh\s+pr\s+merge\b(?:\s+(?!-)(\S+))?")


def read(path: Path) -> Iterator[dict[str, Any]]:
    """Every entry of a transcript, one line at a time, so a transcript of hundreds of
    megabytes is never held whole; a line still being written is skipped."""
    try:
        with path.open("rb") as f:
            for raw in f:
                try:
                    entry = json.loads(raw)
                except ValueError:
                    continue
                if isinstance(entry, dict):
                    yield entry
    except OSError:
        return


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
    rest = command[commit.start() :].lstrip(" \t\n;&|(")  # from git itself, not the step before it
    first = rest.split("\n", 1)[0]
    if "--amend" in first:
        return "commit", "amended the last commit"
    message = MESSAGE.search(first)
    if message:
        return "commit", message.group(1) or message.group(2)
    own = re.split(r"[;&|]", first, maxsplit=1)[0]  # the commit's own step, not what follows on the line
    heredoc = HEREDOC.search(rest) if re.search(r"(?:-F|--file)[ =]-(?:\s|$)", own) else None
    if heredoc and heredoc.start() <= len(own) and heredoc.group(2).strip():
        return "commit", heredoc.group(2).strip()
    return "commit", "a commit"


def _error_text(block: dict[str, Any]) -> str:
    """The first line of a failed tool's result, which may come in parts."""
    content = block.get("content")
    if isinstance(content, list):
        content = " ".join(str(c.get("text", "")) for c in content if isinstance(c, dict))
    return str(content or "a tool failed").strip().splitlines()[0]


class _Reading:
    """What `digest` gathers while it walks a transcript, one entry at a time."""

    def __init__(self) -> None:
        self.timeline: list[dict[str, Any]] = []
        self.tools: Counter[str] = Counter()
        self.helpers: dict[str, str | None] = {}  # the helper call's id -> its background id, if any
        self.answered: set[str] = set()
        self.handed_back: set[str] = set()
        self.linked: set[Any] = set()

    def entry(self, entry: dict[str, Any]) -> None:
        at, kind = entry.get("timestamp"), entry.get("type")
        typed = _typed(entry)
        if typed:
            self.timeline.append(_item(at, "prompt", typed))
        if kind == "pr-link":
            self._pr_link(at, entry)
        if kind == "system" and entry.get("subtype") == "compact_boundary":
            self.timeline.append(_item(at, "compact", "The conversation was compacted"))
        self._hand_back(kind, entry)
        # Claude Code also keeps copies of turns (such as api-request-blob); only the turns count.
        for block in _blocks(entry) if kind in ("user", "assistant") else []:
            if block.get("type") == "tool_use":
                self._tool_use(at, block)
            elif block.get("type") == "tool_result":
                self._tool_result(at, entry, block)

    def _pr_link(self, at: Any, entry: dict[str, Any]) -> None:
        # Claude Code links the same pull request again and again; it was opened once.
        key = entry.get("prUrl") or entry.get("prNumber")
        if key in self.linked:
            return
        self.linked.add(key)
        number = entry.get("prNumber")
        self.timeline.append(_item(at, "pr_opened", f"#{number}" if number else "pull request", url=entry.get("prUrl")))

    def _hand_back(self, kind: Any, entry: dict[str, Any]) -> None:
        origin = entry.get("origin") or {}
        if isinstance(origin, dict) and origin.get("senderTaskId"):
            self.handed_back.add(str(origin["senderTaskId"]))
        if kind == "queue-operation":
            self.handed_back.update(re.findall(r"<task-id>([^<]+)</task-id>", str(entry.get("content", ""))))

    def _tool_use(self, at: Any, block: dict[str, Any]) -> None:
        name, given = str(block.get("name", "")), block.get("input") or {}
        self.tools[name] += 1
        found: tuple[str, str] | None = None
        if name in HELPERS:
            self.helpers[str(block.get("id"))] = None
        elif name == "AskUserQuestion":
            first = (given.get("questions") or [{}])[0]
            found = "question", str(first.get("question", "")) or "a question"
        elif name == "Bash":
            found = _from_command(str(given.get("command", "")))
        if found:
            self.timeline.append(_item(at, *found))

    def _tool_result(self, at: Any, entry: dict[str, Any], block: dict[str, Any]) -> None:
        tool_id = str(block.get("tool_use_id"))
        self.answered.add(tool_id)
        launched = entry.get("toolUseResult") or {}
        if tool_id in self.helpers and isinstance(launched, dict) and launched.get("status") == "async_launched":
            self.helpers[tool_id] = str(launched.get("agentId"))
        if block.get("is_error"):
            self.timeline.append(_item(at, "error", _error_text(block)))

    def running(self) -> int:
        return _running(self.helpers, self.answered, self.handed_back)


def _running(helpers: dict[str, str | None], answered: set[str], handed_back: set[str]) -> int:
    # RULE: a helper counts as running until it has handed back
    return sum(
        1
        for call, background in helpers.items()
        if (background is None and call not in answered) or (background is not None and background not in handed_back)
    )


def digest(entries: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Tool calls per tool, helpers running and in all, and the newest timeline items."""
    reading = _Reading()
    for entry in entries:
        if not entry.get("isSidechain"):
            reading.entry(entry)
    return {
        "tool_calls": {"total": sum(reading.tools.values()), "by_tool": dict(reading.tools.most_common())},
        "subagents": {"running": reading.running(), "total": len(reading.helpers)},
        "timeline": list(reversed(reading.timeline))[:TIMELINE],
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
            reason = str(exc)[:300].rstrip(". ") or type(exc).__name__
            # RULE: a failed summary says what to do next
            self.errors[sid] = f"{reason}. {RETRY}"
            self.publish("summary", {"session": sid, "summary": None, "summary_state": "error"})
            return None
        at = datetime.now(UTC).isoformat(timespec="seconds")
        self.store.set_setting(f"summary:{sid}", {"summary": summary, "at": at})
        self.publish("summary", {"session": sid, "summary": summary, "summary_state": "ready"})
        return summary
