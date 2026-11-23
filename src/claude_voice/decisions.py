"""The manager's check of a yes that the support role recorded.

Support writes each decision of the owner's as one line in decisions.jsonl: the time, the
item, the action, the owner's exact words (quote), support's session id, the uuid of the user
turn that holds them, and the sha the question named. Before it acts, the manager runs
`claude-voice decisions check <uuid> --head <full sha now>`. The check reads support's own
transcript and accepts the line only when:

- the session is support's, from the bridge's role record, never a path in the line;
- the uuid has not been used before;
- the turn is the owner's own (below);
- the quote is the whole of the owner's words in it, not a part of them;
- the assistant turn just before it named the item, the action and the sha;
- the item is still at that sha, given in full.

Whose turn it is. The voice reaches support only through the bridge's courier, so a spoken yes
arrives as a cross-session message. Its sender's pid and start ticks are the kernel's word,
and the bridge keeps those of every courier process it starts. Only the courier's are kept: the
digest and side-question helpers also run in helper folders, but a message from one of them is
never a yes. Such a message counts when they match, and the first line of its body is
`origin: voice`. Any other user turn that starts with
`origin:` wrote the stamp itself, and is refused. A turn with no stamp is the owner's only
when typed in support's own pane: entrypoint "cli", origin kind "human", not a meta entry, and
plain text. In this machine's transcripts, peer messages, task notifications, Stop-hook
feedback and compact summaries also carry entrypoint "cli", so the entrypoint alone does not
tell them apart. A cross-session wrapper inside the owner's words is never a yes.

Every check is logged to decision-checks.jsonl, and an accepted uuid is never accepted again.
The manager runs one check at a time; the log is not locked against two at once. This is a
slip guard, not a wall: the transcript, the log and the bridge's record are files the owner's
own Unix user can write. Design: support-role-v1, v1.1 b1(b), b2, c1 and f2, v1.2 c2, and f4.
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import STATE_DIR
from .store import Store

FIRST = "origin: "
VOICE = "origin: voice"
FOOTER = "Your reply is read by the voice."  # what the bridge adds under each carried message
WRAPPER = "<cross-session-message"
DECISIONS = "decisions.jsonl"
LOG = "decision-checks.jsonl"
COURIERS = "courier:processes"  # the bridge's setting that holds them
KEPT = 500  # courier processes kept; a courier is replaced every twenty messages
SHORT = 8  # how much of a sha support's question shows
FULL_SHA = re.compile(r"[0-9a-f]{40}")
PROJECTS_DIR = Path("~/.claude/projects").expanduser()

Couriers = set[tuple[int, str]]


def remember_courier(store: Store, pid: int, started: str | None) -> None:
    """Keep a courier process the bridge started, as the kernel will name it to a receiver."""
    if started is None:
        return
    held = store.get_setting(COURIERS)
    rows = [r for r in held if isinstance(r, dict)] if isinstance(held, list) else []
    rows.append({"pid": pid, "started": started})
    # RULE: the bridge keeps only the newest courier processes
    store.set_setting(COURIERS, rows[-KEPT:])


def couriers(store: Store) -> Couriers:
    """The courier processes the bridge has started, as (pid, start ticks)."""
    held = store.get_setting(COURIERS)
    rows = held if isinstance(held, list) else []
    return {(r["pid"], r["started"]) for r in rows if isinstance(r, dict) and "pid" in r and "started" in r}


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


def spoken(entry: dict[str, Any], known: Couriers) -> str | None:
    """The owner's words in a message the bridge's courier carried from the voice, or None."""
    origin = entry.get("origin") or {}
    sender = (origin.get("verifiedPeerPid"), origin.get("verifiedPeerProcStart"))
    # RULE: a voice yes comes only from the bridges own courier
    if entry.get("type") != "user" or origin.get("kind") != "peer" or sender not in known:
        return None
    first, _, rest = str(origin.get("body") or "").partition("\n")
    # RULE: only the first line says where a turn came from
    if first != VOICE:
        return None
    words = rest.strip()
    return words.removesuffix(FOOTER).strip()


def owners_words(entry: dict[str, Any], known: Couriers) -> tuple[str, str] | None:
    """Who said the turn, "voice" or "owner", and their words; None when it is not the owner's."""
    words = spoken(entry, known)
    if words is not None:
        who = "voice"
    else:
        text = plain_text(entry)
        # RULE: a stamped turn counts only when the courier carried it
        if text is None or text.startswith(FIRST):
            return None
        # RULE: an unstamped turn counts only when typed in supports own pane
        if not typed_in_pane(entry):
            return None
        who, words = "owner", text.strip()
    # RULE: a cross session wrapper is never a yes
    if WRAPPER in words:
        return None
    return who, words


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
    known: Couriers,
) -> str | None:
    """Why the decision `line` does not count, or None when it does.

    `entries` is support's transcript, read from the role record's session; `used` holds the
    uuids already accepted; `head` is the sha the item is at now; `known` holds the courier
    processes the bridge started.
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
    return turn_refused(line, entries, at, head, known)


def turn_refused(
    line: dict[str, Any], entries: list[dict[str, Any]], at: int, head: str, known: Couriers
) -> str | None:
    """Why the turn at `at` does not carry the decision `line`, or None when it does."""
    said = owners_words(entries[at], known)
    if said is None:
        return "Refused: the turn is not the owner's own words, so it is a peer's claim."
    # RULE: the quote is the owners whole answer
    if not line.get("quote") or str(line.get("quote")).strip() != said[1]:
        return "Refused: the quote is not the whole of the owner's words in that turn."
    return question_refused(line, question_before(entries, at), head)


def question_refused(line: dict[str, Any], question: str, head: str) -> str | None:
    """Why the yes does not answer `question` about the item at `head`, or None when it does."""
    sha, item, action = (str(line.get(k) or "") for k in ("sha", "item", "action"))
    # RULE: the head is a full sha
    if not FULL_SHA.fullmatch(head):
        return "Refused: --head must be the item's full 40-character sha."
    # RULE: the yes answers the question that named the sha
    if len(sha) < SHORT or sha[:SHORT] not in question:
        return "Refused: the turn does not answer a question that named this sha."
    # RULE: the question named the item and the action
    if not item or not action or item not in question or action.lower() not in question.lower():
        return "Refused: the question before the turn did not name this item and action."
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
    store = Store(state / "bridge.db")
    support = store.get_setting("role:support")
    entries = transcript(str(support["id"]), projects_dir) if support and support.get("id") else []
    used = {r.get("uuid") for r in read_jsonl(state / LOG) if r.get("result") == "accepted"}
    known = couriers(store)
    why = check(line, support, entries, {u for u in used if u}, head, known)
    log(state / LOG, line, entries, head, why, known)
    sys.stdout.write(f"claude-voice: {why or 'accepted: the owner said it, in support, about this sha.'}\n")
    return 1 if why else 0


def log(
    path: Path, line: dict[str, Any], entries: list[dict[str, Any]], head: str, why: str | None, known: Couriers
) -> None:
    """One line per check, so the owner can read after what was checked and why."""
    turn = next((e for e in entries if e.get("uuid") == line.get("uuid")), {})
    who, words = owners_words(turn, known) or (None, None)
    row = {
        "at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "decision": line,
        "uuid": line.get("uuid"),
        "origin": who,
        "quote_found": words is not None and str(line.get("quote") or "").strip() == words,
        "sha_matched": line.get("sha") == head,
        "result": "refused" if why else "accepted",
        "why": why,
    }
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")
