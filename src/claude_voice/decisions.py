"""The manager's check of a yes that the support role recorded.

Support writes each decision of the owner's as one line in decisions.jsonl: the time, the
item, the owner's exact words (quote), support's session id, the uuid of the user turn
that holds them, and the sha the question named. Before it acts, the manager runs
`claude-voice decisions check <uuid> --head <sha now>`. The check reads support's own
transcript and accepts the line only when:

- the session is support's, from the bridge's role record, never a path in the line;
- the uuid has not been used before;
- the turn is the owner's own (below);
- it holds the quoted words;
- the assistant turn just before it asked about this sha;
- the item is still at that sha.

Whose turn it is. The origin is read from the turn's first line only, as the bridge stamps it:
`origin: voice` is the owner's voice; any other stamp is not the owner's. A turn with no stamp
is the owner's only when it was typed in support's own pane: entrypoint "cli", origin kind
"human", not a meta entry, and plain text. In this machine's transcripts, peer messages, task
notifications, Stop-hook feedback and compact summaries also carry entrypoint "cli", so the
entrypoint alone does not tell them apart. A turn the SDK delivered carries "sdk-py" or
"sdk-cli" and rests on its stamp alone. A cross-session wrapper is never a yes.

Every check is logged to decision-checks.jsonl, and an accepted uuid is never accepted again.
This is a slip guard, not a wall: the transcript and the log are files the owner's own Unix
user can write. Design: support-role-v1, v1.1 b1(b), b2, c1 and f2, v1.2 c2, and f4.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import STATE_DIR
from .store import Store

FIRST = "origin: "
VOICE = "origin: voice"
WRAPPER = "<cross-session-message"
DECISIONS = "decisions.jsonl"
LOG = "decision-checks.jsonl"
SHORT = 8  # how much of a sha support's question shows
PROJECTS_DIR = Path("~/.claude/projects").expanduser()


def plain_text(entry: dict[str, Any]) -> str | None:
    """The words of a user turn, or None when the entry is not one a person wrote."""
    if entry.get("type") != "user" or entry.get("isMeta") or entry.get("isCompactSummary"):
        return None
    content = (entry.get("message") or {}).get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list) and content and all(isinstance(b, dict) and b.get("type") == "text" for b in content):
        return "\n".join(b.get("text", "") for b in content)
    return None


def typed_in_pane(entry: dict[str, Any]) -> bool:
    """Whether an unstamped turn was typed in support's own pane."""
    return entry.get("entrypoint") == "cli" and (entry.get("origin") or {}).get("kind") == "human"


def who_said(entry: dict[str, Any]) -> str | None:
    """Who said the turn: "voice" or "owner" when it is the owner's own, else None."""
    text = plain_text(entry)
    # RULE: a cross session wrapper is never a yes
    if text is None or WRAPPER in text:
        return None
    first = text.partition("\n")[0]
    # RULE: only the first line says where a turn came from
    if first.startswith(FIRST):
        return "voice" if first == VOICE else None
    # RULE: an unstamped turn counts only when typed in supports own pane
    if typed_in_pane(entry):
        return "owner"
    return None


def question_before(entries: list[dict[str, Any]], at: int) -> str:
    """The text of the last assistant turn before entry `at`."""
    for entry in reversed(entries[:at]):
        content = (entry.get("message") or {}).get("content")
        if entry.get("type") == "assistant" and isinstance(content, list):
            texts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
            if any(texts):
                return "\n".join(texts)
    return ""


def check(
    line: dict[str, Any],
    support: dict[str, Any] | None,
    entries: list[dict[str, Any]],
    used: set[str],
    head: str,
) -> str | None:
    """Why the decision `line` does not count, or None when it does.

    `entries` is support's transcript, read from the role record's session; `used` holds the
    uuids already accepted; `head` is the sha the item is at now.
    """
    uuid = line.get("uuid")
    # RULE: the transcript is supports own from the bridges role record
    if not support or not support.get("id") or line.get("session") != support.get("id"):
        return "Refused: the line does not name the running support session."
    # RULE: each uuid is used once
    if uuid in used:
        return "Refused: that uuid was used for a decision before."
    at = next((i for i, e in enumerate(entries) if e.get("uuid") == uuid), None)
    if at is None:
        return "Refused: support's transcript has no turn with that uuid."
    return turn_refused(line, entries, at, head)


def turn_refused(line: dict[str, Any], entries: list[dict[str, Any]], at: int, head: str) -> str | None:
    """Why the turn at `at` does not carry the decision `line`, or None when it does."""
    turn = entries[at]
    if who_said(turn) is None:
        return "Refused: the turn is not the owner's own words, so it is a peer's claim."
    quote = str(line.get("quote") or "")
    # RULE: the turn holds the quoted words
    if not quote or quote not in (plain_text(turn) or ""):
        return "Refused: the turn does not hold the quoted words."
    sha = str(line.get("sha") or "")
    # RULE: the yes answers the question that named the sha
    if len(sha) < SHORT or sha[:SHORT] not in question_before(entries, at):
        return "Refused: the turn does not answer a question that named this sha."
    # RULE: the item is still at the sha it was asked about
    if sha != head:
        return "Refused: the item has moved since the owner was asked, so support asks again."
    return None


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    """The JSON objects in `path`, one per line; unreadable lines and a missing file give none."""
    try:
        raw = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    rows = []
    for text in raw:
        try:
            row = json.loads(text)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def transcript(session_id: str, projects_dir: Path) -> list[dict[str, Any]]:
    """Support's transcript, found by the session id alone."""
    found = sorted(projects_dir.glob(f"*/{session_id}.jsonl"))
    return read_jsonl(found[0]) if found else []


def run(uuid: str, head: str, state_dir: Path = STATE_DIR, projects_dir: Path = PROJECTS_DIR) -> int:
    """Check the decision line with `uuid`, log the check, and say the result. 0 means accepted."""
    state = state_dir.expanduser()
    line = next((r for r in reversed(read_jsonl(state / DECISIONS)) if r.get("uuid") == uuid), None)
    if line is None:
        sys.stdout.write("claude-voice: Refused: there is no decision line with that uuid.\n")
        return 1
    support = Store(state / "bridge.db").get_setting("role:support")
    entries = transcript(str(support["id"]), projects_dir) if support and support.get("id") else []
    used = {r.get("uuid") for r in read_jsonl(state / LOG) if r.get("result") == "accepted"}
    why = check(line, support, entries, {u for u in used if u}, head)
    log(state / LOG, line, entries, head, why)
    sys.stdout.write(f"claude-voice: {why or 'accepted: the owner said it, in support, about this sha.'}\n")
    return 1 if why else 0


def log(path: Path, line: dict[str, Any], entries: list[dict[str, Any]], head: str, why: str | None) -> None:
    """One line per check, so the owner can read after what was checked and why."""
    turn = next((e for e in entries if e.get("uuid") == line.get("uuid")), {})
    row = {
        "at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "decision": line,
        "uuid": line.get("uuid"),
        "origin": who_said(turn),
        "quote_found": bool(line.get("quote")) and str(line.get("quote")) in (plain_text(turn) or ""),
        "sha_matched": line.get("sha") == head,
        "result": "refused" if why else "accepted",
        "why": why,
    }
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")
