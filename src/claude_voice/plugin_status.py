"""Every plugin asked how it is, at once, with a short limit each (#518).

Once one plugin's status call hung for minutes while the others answered, and finding which
one took a lot of back and forth. Each plugin's status tool is called in parallel. Whatever
has not answered when the limit is up is named as too slow, and the answer goes back at once.
Read-only: only a plugin's status tool is ever called, and with no arguments.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from .plugins import as_object

#: How long each plugin has to answer, in seconds.
SECONDS = 4.0
#: The longest one-line state the voice is given, in characters.
LINE = 120
#: Calls that were still running when the answer went back. Each is left to finish, since
#: cutting one off midway can leave the plugin's connection half used; it is let go then.
LEFT_RUNNING: set[asyncio.Future[Any]] = set()


def status_tool(tools: list[str]) -> str | None:
    """The plugin's own status tool: one named `status`, else the first ending in `_status`."""
    if "status" in tools:
        return "status"
    return next((t for t in tools if t.endswith("_status")), None)


def one_line(answer: Any) -> str:
    """A plugin's answer as one short line: its `say` or text when it has one, else its JSON."""
    if isinstance(answer, dict) and isinstance(answer.get("say") or answer.get("text"), str):
        said = answer.get("say") or answer["text"]
    else:
        said = json.dumps(answer, ensure_ascii=False)
    # RULE: each answer is cut to one short line
    line = next((part.strip() for part in said.splitlines() if part.strip()), "")
    return line if len(line) <= LINE else line[: LINE - 1].rstrip() + "…"


async def _ask(plugin: Any, tool: str) -> str:
    """The plugin's state in one line, or RuntimeError with the plugin's reason."""
    answer = await plugin.call(tool, {})
    text = "\n".join(getattr(c, "text", "") for c in answer.content or [] if getattr(c, "text", None))
    if answer.is_error:
        raise RuntimeError(text or f"{plugin.spec.name} refused")
    found = answer.structured_content if answer.structured_content is not None else as_object(text)
    return one_line(plugin.shown(found))


def _let_go(job: asyncio.Future[Any]) -> None:
    LEFT_RUNNING.discard(job)
    if not job.cancelled():
        job.exception()  # read, so a late failure is not reported as never retrieved


async def check(plugins: list[Any], seconds: float = SECONDS) -> dict[str, Any]:
    """Who answered and how each is, who failed and why, and who was too slow."""
    if not plugins:
        return {"answered": [], "failed": [], "too_slow": [], "no_status_tool": [], "say": "No plugins are running."}
    jobs: dict[str, asyncio.Future[str]] = {}
    no_status = []
    for plugin in plugins:
        tool = status_tool(list(plugin.spec.tools))
        # RULE: a plugin without a status tool is named and not called
        if tool is None:
            no_status.append(plugin.spec.name)
            continue
        jobs[plugin.spec.name] = asyncio.ensure_future(_ask(plugin, tool))
    # RULE: every plugin is asked at once and a slow one is named without holding up the answer
    if jobs:
        await asyncio.wait(jobs.values(), timeout=seconds)
    answered, failed, slow = [], [], []
    for name, job in jobs.items():
        if not job.done():
            slow.append(name)
            # RULE: a slow plugins call is left to finish and never cut off
            LEFT_RUNNING.add(job)
            job.add_done_callback(_let_go)
        elif job.exception() is not None:
            # RULE: a plugin that fails is named with its reason
            failed.append({"plugin": name, "problem": str(job.exception())})
        else:
            answered.append({"plugin": name, "state": job.result()})
    return {
        "answered": answered,
        "failed": failed,
        "too_slow": slow,
        "no_status_tool": no_status,
        "say": _say(len(jobs), answered, failed, slow),
    }


def _say(asked: int, answered: list[dict[str, str]], failed: list[dict[str, str]], slow: list[str]) -> str:
    """One short sentence: how many answered, and the names of those that did not."""
    parts = [f"{len(answered)} of {asked} plugins answered"]
    if failed:
        parts.append("failed: " + ", ".join(f["plugin"] for f in failed))
    if slow:
        parts.append("too slow: " + ", ".join(slow))
    return "; ".join(parts) + "."
