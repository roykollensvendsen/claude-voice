"""Stop hook: hold a turn back once if another session's message is still unanswered.

Claude Code runs this as a session is about to end its turn, with the session's
transcript path on stdin. If a message from another session arrived and no
SendMessage has gone back to its sender since, the turn is held back with the
message named, so the session answers it or says why it will not.

It can never trap a session:
- the second stop in a row is always let through (Claude Code's stop_hook_active);
- each message holds a turn back at most once, ever, remembered per session;
- anything unreadable is let through.

Couriers named claude-voice-msg-* (and the older tmp-*) deliver one message for
the claude-voice bridge and exit at once, so they cannot receive an answer. The
answer to them belongs in the session's own reply, which the bridge reads.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import re
import sys
from typing import IO, Any

STATE_DIR = pathlib.Path("~/.local/state/claude-voice/unanswered-peer").expanduser()
SESSIONS_DIR = pathlib.Path("~/.claude/sessions").expanduser()
SOCKET_PID = re.compile(r"/(\d+)\.sock$")
COURIERS = ("claude-voice-msg-", "tmp-")
MESSAGE = re.compile(
    r'<cross-session-message from="(?P<address>[^"]*)" from-name="(?P<name>[^"]*)"[^>]*>'
    r"\n?(?P<text>.*?)</cross-session-message>",
    re.DOTALL,
)


def _texts(entry: dict[str, Any]) -> list[str]:
    """The places a peer message can appear: a user turn, or a queued prompt."""
    if entry.get("type") == "user":
        content = entry.get("message", {}).get("content")
        if isinstance(content, str):
            return [content]
        if isinstance(content, list):
            return [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
    if entry.get("type") == "attachment":
        prompt = entry.get("attachment", {}).get("prompt")
        return [prompt] if isinstance(prompt, str) else []
    return []


def _replies(entry: dict[str, Any]) -> list[str]:
    if entry.get("type") != "assistant":
        return []
    content = entry.get("message", {}).get("content") or []
    return [
        str(b.get("input", {}).get("to", ""))
        for b in content
        if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("name") == "SendMessage"
    ]


def _is_courier(name: str, address: str) -> bool:
    """A courier by its name, or by its working folder, whatever name the owner gave it."""
    if name.startswith(COURIERS):
        return True
    pid = SOCKET_PID.search(address)
    if not pid:
        return False
    try:
        cwd = json.loads((SESSIONS_DIR / f"{pid.group(1)}.json").read_text()).get("cwd", "")
    except (OSError, ValueError, AttributeError):
        return False
    return pathlib.Path(str(cwd)).name.startswith(COURIERS)


def unanswered(entries: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Peer messages with no SendMessage to their sender after them, oldest first."""
    pending: dict[str, dict[str, str]] = {}
    for entry in entries:
        for to in _replies(entry):
            for key in [k for k, m in pending.items() if to in (m["name"], m["address"])]:
                del pending[key]
        for text in _texts(entry):
            for match in MESSAGE.finditer(text):
                message = {k: match.group(k).strip() for k in ("address", "name", "text")}
                if _is_courier(message["name"], message["address"]):
                    continue
                key = hashlib.sha256(json.dumps(message, sort_keys=True).encode()).hexdigest()
                pending.setdefault(key, {**message, "key": key})
    return list(pending.values())


def main(stdin: IO[str] = sys.stdin, stdout: IO[str] = sys.stdout) -> int:
    try:
        event = json.load(stdin)
        if event.get("stop_hook_active"):
            return 0
        lines = pathlib.Path(event["transcript_path"]).read_text().splitlines()
        entries = [json.loads(line) for line in lines if line.strip()]
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return 0

    state = STATE_DIR / f"{event.get('session_id', 'unknown')}.json"
    try:
        nagged = set(json.loads(state.read_text()))
    except (OSError, ValueError):
        nagged = set()
    fresh = [m for m in unanswered(entries) if m["key"] not in nagged]
    if not fresh:
        return 0

    try:
        state.parent.mkdir(parents=True, exist_ok=True)
        state.write_text(json.dumps(sorted(nagged | {m["key"] for m in fresh})))
    except OSError:
        return 0  # without memory this could hold a turn back more than once
    lines_out = [f'- {m["name"]} ({m["address"]}): "{m["text"].splitlines()[0][:200]}"' for m in fresh]
    reason = (
        "Unanswered message(s) from another Claude session:\n"
        + "\n".join(lines_out)
        + "\nReply with SendMessage (to the name or address above) before ending, or say in one "
        "line why no reply is needed. This reminder comes once per message."
    )
    stdout.write(json.dumps({"decision": "block", "reason": reason}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
