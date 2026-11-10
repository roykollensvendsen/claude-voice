"""Where a plugin already approved is restarted, and approved again by the owner's voice.

ADR-CV-010. A plugin whose tools take the same inputs is started again with its new code
and nothing is asked. A plugin whose tools changed is approved by voice only in developer
mode, only from the Claude app (where the owner talks), and only when its checkout is clean
and level with the branch it follows. A plugin never approved, or one with a new command or
folder, is still approved by typing its name in a terminal.

Every answer has `ok` and `say`, a sentence for the voice to read out as it is.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from . import plugins
from .config import PluginSpec
from .devmode import DeveloperMode, Questions

log = logging.getLogger("claude_voice")
VOICE_ONLY = "Only the owner's voice, in the Claude app, can do that."


def _no(say: str) -> dict[str, Any]:
    return {"ok": False, "say": say}


def _typed(name: str) -> str:
    return f"type it in a terminal: claude-voice plugins approve {name}"


class Desk:
    """Restarts and voice approvals for the bridge's plugins."""

    def __init__(
        self,
        server: Any,
        active: list[plugins.Plugin],
        specs: Callable[[], list[PluginSpec]],
        approvals: Path | None,
        client: Callable[[Any], Any] | None,
        git: Callable[[str], dict[str, Any] | None],
        mode: DeveloperMode,
        questions: Questions,
        announce: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self.server, self.active, self.specs = server, active, specs
        self.approvals, self.client, self.git = approvals, client, git
        self.mode, self.questions = mode, questions
        self.announce = announce  # tells connected clients that the tool list changed
        self._seen = plugins.read_approvals(approvals)

    def _spec(self, name: str) -> PluginSpec | None:
        return next((s for s in self.specs() if s.name == name), None)

    def _commit(self, folder: str) -> str | None:
        status = self.git(folder) or {}
        return (status.get("commit") or {}).get("hash")

    # -- restarting --------------------------------------------------------------------

    async def restart(self, name: str) -> dict[str, Any]:
        """Stop the plugin and start it again with the code in its folder now; its tools are
        offered again only if they are as the owner approved them."""
        spec = self._spec(name)
        if spec is None:
            return _no(f"There is no plugin called {name}.")
        for old in [p for p in self.active if p.spec.name == name]:
            plugins.withdraw(self.server, old)
            await old._drop()
            old.state = "stopped"
            self.active.remove(old)
        plugin, why = await asyncio.to_thread(
            plugins.start_one, self.server, spec, self.approvals, self.client, self._commit
        )
        if self.announce is not None:
            with contextlib.suppress(Exception):
                await self.announce()
        if plugin is None:
            log.info("plugin %s restarted but not offered: %s", name, why)
            how = "approve it by voice in developer mode, or " if why == "tools changed" else ""
            return _no(f"{name} was not started again: {why}. To use it, {how}{_typed(name)}.")
        self.active.append(plugin)
        log.info("plugin %s restarted by voice", name)
        return {"ok": True, "say": f"{name} started again with its new code.", "tools": plugin.offered}

    async def pick_up(self) -> list[str]:
        """Start again each plugin whose approval changed in the file since the last look,
        such as one the owner just typed, so it takes effect without restarting the bridge;
        the names started again."""
        now = plugins.read_approvals(self.approvals)
        changed = sorted(n for n in {*now, *self._seen} if now.get(n) != self._seen.get(n))
        self._seen = now
        for name in changed:
            if self._spec(name) is not None:
                await self.restart(name)
        return changed

    # -- developer mode ----------------------------------------------------------------

    def mode_request(self, caller: str) -> dict[str, Any]:
        # RULE: only the owners voice in the claude app can approve a plugin or switch developer mode on
        if caller != "claude_app":
            return _no(VOICE_ONLY)
        token = self.questions.ask("developer_mode", {})
        return {
            "ok": True,
            "say": "Switch developer mode on for eight hours? While it is on, you can approve plugins "
            "whose tools changed by voice. Say yes to switch it on.",
            "token": token,
        }

    def mode_confirm(self, caller: str, token: str) -> dict[str, Any]:
        if caller != "claude_app":
            return _no(VOICE_ONLY)
        if self.questions.take("developer_mode", token) is None:
            return _no("That question has run out or was answered. Ask again.")
        state = self.mode.switch_on()
        return {"ok": True, "say": f"Developer mode is on until {state['until']}.", **state}

    def mode_off(self, caller: str) -> dict[str, Any]:
        state = self.mode.switch_off(caller)
        return {"ok": True, "say": "Developer mode is off.", **state}

    # -- approving a changed plugin by voice -------------------------------------------

    def _refusal(self, caller: str, spec: PluginSpec | None, name: str) -> str | None:
        if caller != "claude_app":
            return VOICE_ONLY
        # RULE: a changed plugin is approved by voice only in developer mode
        if not self.mode.state()["on"]:
            return f"Developer mode is off. Switch it on first, or {_typed(name)}."
        if spec is None:
            return f"There is no plugin called {name}."
        if not plugins.known_place(spec, self.approvals):
            return f"{name} is new, or runs a new command or folder, so {_typed(name)}."
        why = plugins.checkout_refusal(self.git(spec.cwd), spec.branch)
        return f"{name} can't be approved by voice: {why}." if why else None

    async def approve_request(self, caller: str, name: str) -> dict[str, Any]:
        spec = self._spec(name)
        refused = self._refusal(caller, spec, name)
        if refused or spec is None:
            return _no(refused or f"There is no plugin called {name}.")
        listing = await asyncio.to_thread(plugins.Plugin(spec, self.client or plugins.CLIENT).listing)
        if not listing:
            return _no(f"{name} did not start, so there is nothing to approve.")
        if plugins.approved(spec, self.approvals) and plugins.inputs_approved(spec, listing, self.approvals):
            return _no(f"Nothing in {name} needs approving. To use its new code, call plugin_restart.")
        said = plugins.changes(spec, listing, self.approvals)
        commit = self._commit(spec.cwd)
        token = self.questions.ask("plugin", {"name": name, "listing": listing, "commit": commit})
        return {
            "ok": True,
            "say": f"{name} at commit {commit} has changed tools: {'; '.join(said)}. Approve them?",
            "changes": said,
            "token": token,
        }

    async def approve_confirm(self, caller: str, token: str) -> dict[str, Any]:
        if caller != "claude_app":
            return _no(VOICE_ONLY)
        asked = self.questions.take("plugin", token)
        if asked is None:
            return _no("That question has run out or was answered. Ask again.")
        name = str(asked["name"])
        spec = self._spec(name)
        refused = self._refusal(caller, spec, name)
        if refused or spec is None:
            return _no(refused or f"There is no plugin called {name}.")
        if self._commit(spec.cwd) != asked["commit"]:
            return _no(f"{name}'s code changed after the question, so nothing was approved. Ask again.")
        plugins.approve(spec, asked["listing"], self.approvals)
        self._seen = plugins.read_approvals(self.approvals)  # started below, not picked up again
        log.info("plugin %s approved by voice at %s", name, asked["commit"])
        started = await self.restart(name)
        return {**started, "say": f"Approved. {started['say']}"}
