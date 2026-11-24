"""Every plugin asked how it is, at once, with a short limit each (#518).

Once one plugin's status call hung for minutes while the others answered, and finding which
one took a lot of back and forth. Each plugin's status tool is called in parallel. Whatever
has not answered when the limit is up is named as too slow, and the answer goes back at once.

Only a status tool the plugin's own listing marks read-only is called, and with no arguments.
These plugins drive real devices, so a name alone never decides what is called.
"""

from __future__ import annotations

import asyncio
import functools
import json
from typing import Any

from .plugins import as_object

#: How long each plugin has to answer, in seconds.
SECONDS = 4.0
#: The longest one-line state the voice is given, in characters.
LINE = 120
#: Each plugin's status call that was still running when an answer went back, by plugin name.
#: It is left to finish, since cutting one off midway can leave the plugin's connection half
#: used. Until it finishes the plugin is not asked again, so calls never pile up on its lock
#: in front of the owner's own commands.
RUNNING: dict[str, asyncio.Future[Any]] = {}
NOT_READ_ONLY = "its status tool is not marked read-only"
NO_TOOL = "it has no status tool"


def status_tool(tools: list[str]) -> str | None:
    """The plugin's own status tool: one named `status`, else the shortest name ending in
    `_status`, so `vacuum_status` wins over `vacuum_full_status` in any order. Between two
    of the same length, the first in the plugin's settings wins."""
    if "status" in tools:
        return "status"
    named = [t for t in tools if t.endswith("_status")]
    # RULE: the shortest status tool name is picked whatever the order
    return min(named, key=len) if named else None


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


def _let_go(name: str, job: asyncio.Future[Any]) -> None:
    if RUNNING.get(name) is job:
        del RUNNING[name]
    if not job.cancelled():
        job.exception()  # read, so a late failure is not reported as never retrieved


def _why_not(plugin: Any) -> tuple[str | None, str | None]:
    """The status tool to call, or None and the reason it is not called."""
    tool = status_tool(list(plugin.spec.tools))
    if tool is None:
        return None, NO_TOOL
    # RULE: only a status tool the listing marks read-only is called
    if tool not in getattr(plugin, "looks", set()):
        return None, NOT_READ_ONLY
    return tool, None


async def check(plugins: list[Any], seconds: float = SECONDS) -> dict[str, Any]:
    """Who answered and how each is, who failed and why, and who was too slow."""
    if not plugins:
        return {
            "answered": [],
            "failed": [],
            "too_slow": [],
            "still_busy": [],
            "no_status_tool": [],
            "say": "No plugins are running.",
        }
    jobs: dict[str, asyncio.Future[str]] = {}
    skipped, busy = [], []
    for plugin in plugins:
        name = plugin.spec.name
        tool, why = _why_not(plugin)
        # RULE: a plugin without a read-only status tool is named and not called
        if tool is None:
            skipped.append({"plugin": name, "why": why})
        # RULE: a plugin still busy from an earlier check is not asked again
        elif name in RUNNING:
            busy.append(name)
        else:
            jobs[name] = asyncio.ensure_future(_ask(plugin, tool))
    # RULE: every plugin is asked at once and a slow one is named without holding up the answer
    if jobs:
        await asyncio.wait(jobs.values(), timeout=seconds)
    answered, failed, slow = [], [], []
    for name, job in jobs.items():
        if not job.done():
            slow.append(name)
            # RULE: a slow plugins call is left to finish and never cut off
            RUNNING[name] = job
            job.add_done_callback(functools.partial(_let_go, name))
        elif job.exception() is not None:
            # RULE: a plugin that fails is named with its reason
            failed.append({"plugin": name, "problem": str(job.exception())})
        else:
            answered.append({"plugin": name, "state": job.result()})
    return {
        "answered": answered,
        "failed": failed,
        "too_slow": slow + busy,
        "still_busy": busy,
        "no_status_tool": skipped,
        "say": _say(len(jobs) + len(busy), answered, failed, slow + busy),
    }


def _say(asked: int, answered: list[dict[str, str]], failed: list[dict[str, str]], slow: list[str]) -> str:
    """One short sentence: how many answered, and the names of those that did not."""
    parts = [f"{len(answered)} of {asked} plugins answered"]
    if failed:
        parts.append("failed: " + ", ".join(f["plugin"] for f in failed))
    if slow:
        parts.append("too slow: " + ", ".join(slow))
    return "; ".join(parts) + "."
