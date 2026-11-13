"""Let the owner answer a session's question or approve its tool from the phone.

Claude Code runs this hook before it shows a question (a PreToolUse hook on
AskUserQuestion) or a permission prompt (a PermissionRequest hook), in every
session. It hands the prompt to the bridge. When the companion app is open the
bridge says to wait, and the hook waits for the phone's answer and gives it back
to Claude Code, so the box never shows. When the app is not open, when no answer
comes in time, or when anything goes wrong, the hook steps aside and the box
shows in the terminal exactly as before. It never stops a session.

It also steps aside, without asking the bridge, for a call with several
questions (the phone shows one), and for the session the voice is talking to,
where another hook refuses the box at once and must not be kept waiting.

Installed in the owner's own Claude Code settings; run as
``python -m claude_voice.phone_hook`` with Claude Code's hook input on stdin.
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Protocol

TIMEOUT = 300  # seconds the hook waits for the phone before the box shows at the screen
ENV_FILE = Path("~/.config/claude-voice/env")


class Bridge(Protocol):
    def open(self, body: dict[str, Any]) -> dict[str, Any]: ...
    def wait_for(self, prompt_id: str, seconds: float) -> dict[str, Any] | None: ...
    def close(self, prompt_id: str, how: str) -> None: ...


def handle(hook_input: dict[str, Any], bridge: Bridge, timeout: float = TIMEOUT, spoken_to: str = "") -> dict[str, Any]:
    """Claude Code's hook answer: the phone's answer, or nothing so the box shows.

    ``spoken_to`` is the session the voice is talking to, if any (see ``spoken_to()``)."""
    body = _prompt(hook_input, spoken_to)
    if body is None:
        return {}
    answer = _ask_phone(body, bridge, timeout)
    if answer is None:
        return {}
    if body["kind"] == "question":
        return _question_answer(hook_input.get("tool_input") or {}, answer)
    return _approval_answer(answer)


def _prompt(hook_input: dict[str, Any], spoken_to: str) -> dict[str, Any] | None:
    """What to show on the phone, or None for a hook event the phone does not answer."""
    event = hook_input.get("hook_event_name")
    tool = str(hook_input.get("tool_name") or "")
    tool_input = hook_input.get("tool_input") or {}
    session = str(hook_input.get("session_id") or "")
    if event == "PreToolUse" and tool == "AskUserQuestion":
        return _question(session, tool_input, spoken_to)
    # RULE: a question never opens a second prompt as an approval
    if event == "PermissionRequest" and tool != "AskUserQuestion":
        return {"session_id": session, "kind": "approval", "tool": tool, "input": tool_input}
    return None


def _question(session: str, tool_input: dict[str, Any], spoken_to: str) -> dict[str, Any] | None:
    """A question to show on the phone, or None when it belongs on the screen."""
    # RULE: a call with several questions goes to the screen
    if len(tool_input.get("questions") or []) > 1:
        return None
    # RULE: the hook leaves alone the session the voice is talking to
    if spoken_to and session == spoken_to:
        return None
    return {"session_id": session, "kind": "question", "question": tool_input}


def _ask_phone(body: dict[str, Any], bridge: Bridge, timeout: float) -> dict[str, Any] | None:
    """The phone's answer, or None when nobody is looking, none came in time, or the bridge failed."""
    try:
        opened = bridge.open(body)
    except Exception:
        return None  # a bridge that is down or broken never stops a session
    prompt_id = str(opened.get("id"))
    try:
        if not opened.get("wait"):
            # RULE: the hook closes a prompt it steps aside from
            bridge.close(prompt_id, "screen")
            return None  # nobody is looking at the phone: the box shows as before
        answer = bridge.wait_for(prompt_id, timeout)
        if answer is None:
            bridge.close(prompt_id, "timeout")
        return answer
    except Exception:
        with contextlib.suppress(Exception):
            bridge.close(prompt_id, "screen")  # the box shows, so the phone stops showing it
        return None


def _question_answer(tool_input: dict[str, Any], answer: dict[str, Any]) -> dict[str, Any]:
    questions = tool_input.get("questions") or [{}]
    chosen = answer.get("answer")
    text = chosen.get("text") if isinstance(chosen, dict) else chosen
    value = ", ".join(text) if isinstance(text, list) else str(text)
    updated = {**tool_input, "answers": {str(questions[0].get("question", "")): value}}
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
            "updatedInput": updated,
        }
    }


def _approval_answer(answer: dict[str, Any]) -> dict[str, Any]:
    decision: dict[str, Any] = {"behavior": "allow" if answer.get("decision") == "allow" else "deny"}
    if decision["behavior"] == "deny":
        decision["message"] = str(answer.get("reason") or "Refused from the phone.")
    return {"hookSpecificOutput": {"hookEventName": "PermissionRequest", "decision": decision}}


class HttpBridge:
    """The bridge's prompts API on this computer."""

    def __init__(self, base: str, token: str) -> None:
        self.base, self.token = base.rstrip("/"), token

    def _call(self, method: str, path: str, body: dict[str, Any] | None, timeout: float) -> tuple[int, Any]:
        """The status and body of one request; a refusal is a status, a broken connection raises."""
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(  # noqa: S310 - always this computer's bridge
            self.base + path,
            data=data,
            method=method,
            headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
                raw = response.read()
                return response.status, json.loads(raw) if raw else None
        except urllib.error.HTTPError as refused:
            return refused.code, None

    def open(self, body: dict[str, Any]) -> dict[str, Any]:
        status, opened = self._call("POST", "/api/v1/prompts", body, 5)
        if status != 200 or not isinstance(opened, dict):
            raise OSError(f"the bridge refused the prompt with {status}")
        return opened

    def wait_for(self, prompt_id: str, seconds: float) -> dict[str, Any] | None:
        remaining = seconds
        while remaining > 0:
            chunk = min(remaining, 50)
            status, body = self._call("GET", f"/api/v1/prompts/{prompt_id}/wait?seconds={chunk}", None, chunk + 10)
            # RULE: a prompt the bridge has lost goes back to the screen at once
            if status in (404, 410):
                raise LookupError(f"the bridge no longer knows prompt {prompt_id}")
            # RULE: a wait the bridge refuses goes back to the screen at once
            if status not in (200, 204):
                raise OSError(f"the bridge refused the wait for prompt {prompt_id} with {status}")
            if status == 200 and body:
                return body
            remaining -= chunk
        return None

    def close(self, prompt_id: str, how: str) -> None:
        try:
            self._call("POST", f"/api/v1/prompts/{prompt_id}/close", {"how": how}, 5)
        except (OSError, urllib.error.URLError):
            pass


def spoken_to() -> str:
    """The session the voice is talking to, or nothing: the file the box-refusing hook reads too."""
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    try:
        chosen = json.loads((Path(base) / "voice-bridge" / "target.json").read_text())
    except (OSError, ValueError):
        return ""
    if not isinstance(chosen, dict) or chosen.get("kind") != "session":
        return ""
    return str(chosen.get("claude_session_id") or "")


def _token() -> str:
    token = os.environ.get("CLAUDE_VOICE_TOKEN", "")
    if token:
        return token
    try:
        for line in ENV_FILE.expanduser().read_text().splitlines():
            if line.startswith("CLAUDE_VOICE_TOKEN="):
                return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return ""


def settings_entries(python: str) -> dict[str, list[dict[str, Any]]]:
    """The hook entries for the owner's Claude Code settings, run with the bridge's Python."""
    hook = {"type": "command", "command": f"{python} -m claude_voice.phone_hook", "timeout": TIMEOUT + 30}
    return {
        "PreToolUse": [{"matcher": "AskUserQuestion", "hooks": [hook]}],
        "PermissionRequest": [{"matcher": "*", "hooks": [dict(hook)]}],
    }


def install(settings: Path, python: str) -> None:
    """Add the hook to a Claude Code settings file, keeping every other setting and hook.
    Running it again changes nothing; the file as it was is kept beside it as .bak."""
    try:
        current = json.loads(settings.read_text()) if settings.exists() else {}
    except ValueError as exc:
        raise SystemExit(f"{settings} is not valid JSON; fix it first: {exc}") from None
    if settings.exists():
        settings.with_name(settings.name + ".bak").write_text(settings.read_text())
    hooks = current.setdefault("hooks", {})
    for event, entries in settings_entries(python).items():
        existing = hooks.setdefault(event, [])
        mine = entries[0]["hooks"][0]["command"]
        if not any(mine == h.get("command") for m in existing for h in m.get("hooks", [])):
            existing.extend(entries)
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text(json.dumps(current, indent=2) + "\n")


def main() -> int:
    if sys.argv[1:2] == ["--install"]:
        target = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("~/.claude/settings.json").expanduser()
        install(target, sys.executable)
        print(f"Installed the phone hook in {target}")
        return 0
    try:
        hook_input = json.load(sys.stdin)
    except ValueError:
        return 0
    token = _token()
    if not token:
        return 0
    port = os.environ.get("CLAUDE_VOICE_PORT", "8811")
    out = handle(hook_input, HttpBridge(f"http://127.0.0.1:{port}", token), spoken_to=spoken_to())
    if out:
        sys.stdout.write(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
