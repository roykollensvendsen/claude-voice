"""A session's choice box, read out and answered by voice while the phone hook waits on it.

The phone hook opens a prompt in the book and waits on it; an answer reaches the
session through that wait and no other way. So the voice can answer only while a
hook waits. Once it has stepped aside, the box is on the screen, and the voice says
so. A spoken choice is an option's number or label, several of them, or the
owner's own words, and it must fit the question as the page's answers must.

A terminal session's approval box reaches the voice the same way: listed while a
hook waits on it, and allowed or refused by approve and deny. What it would run is
read out short, with anything that looks like a secret hidden.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from . import prompts, transcripts

log = logging.getLogger("claude_voice.answers")

ON_THE_SCREEN = (
    "That question is not waiting for the voice any more. If it is still open, answer it on the computer's screen."
)
BOXES = frozenset({"input needed", "permission prompt"})  # what Claude Code's live record says a box is
QUEUED = "It's waiting on a question; your message is queued. Shall I read it out?"
QUEUED_APPROVAL = "It's waiting on an approval; your message is queued. Shall I read it out?"
NOTHING_PICKED = "nothing was picked: give an option's number or label, or the owner's own words"
APPROVAL_ON_THE_SCREEN = (
    "That approval is not waiting for the voice any more. If it is still open, answer it on the computer's screen."
)
SPOKEN_INPUT = 120  # characters of what an approval would run that the voice is given
HIDDEN = "[hidden]"
# A value after a word that names a secret: `API_KEY=x`, `--password x`, `token: x`.
NAMED_SECRET = re.compile(
    r"(?i)(?<![\w.-])([\w.-]*(?:token|secret|passw(?:or)?d|api[_-]?key|credential)[\w.-]*)(\s*[=:]\s*|\s+)(\S+)"
)
BEARER_VALUE = re.compile(r"(?i)\b(bearer)\s+[^\s'\"]+")
# A long run of letters and digits, as keys and hashes look; a path's slashes and dots break it.
LONG_RUN = re.compile(r"(?<![A-Za-z0-9_+=-])(?=[A-Za-z0-9_+=-]*\d)[A-Za-z0-9_+=-]{32,}")


def fits(prompt: dict[str, Any], body: dict[str, Any]) -> str | None:
    """Why an answer does not fit its prompt, or None."""
    if prompt["kind"] == "approval":
        if body.get("decision") not in ("allow", "deny"):
            return "an approval is answered with decision allow or deny"
        return None
    answer = body.get("answer")
    if answer is None:
        return "a question is answered with answer"
    if isinstance(answer, dict):
        return None if str(answer.get("text", "")).strip() else "a free answer needs text"
    labels = [o["label"] for o in prompt["question"]["options"]]
    chosen = answer if isinstance(answer, list) else [answer]
    if len(chosen) > 1 and not prompt["question"]["multi"]:
        return "this question takes one choice"
    unknown = [c for c in chosen if c not in labels]
    return f"not an option: {', '.join(map(str, unknown))}" if unknown else None


def settle(prompt: dict[str, Any], body: dict[str, Any], by: str, publish: Any) -> None:
    """Give a prompt its answer, so the hook waiting on it hands it to the session."""
    prompt["answer"] = {k: body[k] for k in ("answer", "decision", "reason") if k in body}
    prompt["done"].set()
    log.info(
        "prompt %s for %s answered %s from %s",
        prompt["id"],
        prompt["session"],
        json.dumps(prompt["answer"], ensure_ascii=False),
        by,
    )
    publish("prompt_closed", {"id": prompt["id"], "how": "voice" if by == "voice" else "phone"})


def numbered(question: dict[str, Any]) -> list[dict[str, Any]]:
    """The options with the numbers the voice reads out, from 1."""
    return [{"number": i, **option} for i, option in enumerate(question["options"], start=1)]


def waiting(book: prompts.Book, live: list[dict[str, Any]], session: str | None = None) -> list[dict[str, Any]]:
    """The questions a hook waits on, each with its session's name and numbered options."""
    names = {str(row.get("sessionId")): row.get("name") for row in live}
    return [
        {
            "id": p["id"],
            "session": p["session"],
            "session_name": names.get(p["session"]) or "a session",
            "text": p["question"]["text"],
            "header": p["question"]["header"],
            "options": numbered(p["question"]),
            "multi": p["question"]["multi"],
            "asked_at": p["asked_at"],
        }
        for p in book.items.values()
        if p["kind"] == "question" and p["answer"] is None and session in (None, p["session"]) and book.hook_waits(p)
    ]


def spoken_input(text: str) -> str:
    """What an approval would run, as the voice is given it: short, and any secret hidden."""
    # RULE: an approval read to the voice never carries what looks like a secret
    hidden = LONG_RUN.sub(HIDDEN, BEARER_VALUE.sub(rf"\1 {HIDDEN}", NAMED_SECRET.sub(rf"\1\2{HIDDEN}", text)))
    return hidden if len(hidden) <= SPOKEN_INPUT else hidden[:SPOKEN_INPUT].rstrip() + "…"


def approvals_waiting(
    book: prompts.Book, live: list[dict[str, Any]], session: str | None = None
) -> list[dict[str, Any]]:
    """The approvals a hook waits on, each with its session's name and what it would run."""
    names = {str(row.get("sessionId")): row.get("name") for row in live}
    return [
        {
            "id": p["id"],
            "session": p["session"],
            "session_name": names.get(p["session"]) or "a session",
            "tool": p.get("tool", ""),
            "input": spoken_input(str(p.get("input", ""))),
            "asked_at": p["asked_at"],
        }
        for p in book.items.values()
        # RULE: an approval a hook waits on is listed for the voice
        if p["kind"] == "approval" and p["answer"] is None and session in (None, p["session"]) and book.hook_waits(p)
    ]


def decide(
    book: prompts.Book, prompt_id: str, decision: str, reason: str | None, publish: Any = None
) -> dict[str, Any]:
    """Allow or refuse an approval a hook waits on, by voice; `decided` is None when it is not one."""
    prompt = book.items.get(prompt_id)
    if prompt is not None and prompt["kind"] != "approval":
        return {"decided": None, "error": "this is a question to answer; use answer_question"}
    # RULE: an approval no hook waits on is refused as on the screen
    if prompt is None or prompt["answer"] is not None or not book.hook_waits(prompt):
        return {"decided": None, "say": APPROVAL_ON_THE_SCREEN}
    body: dict[str, Any] = {"decision": decision}
    if decision == "deny" and reason:
        body["reason"] = reason
    settle(prompt, body, "voice", publish or book.publish)
    return {"decided": decision, "id": prompt_id, "session": prompt["session"], "tool": prompt.get("tool", "")}


def _label(question: dict[str, Any], said: Any) -> Any:
    """The option a spoken choice names, by number or by label in any case; else what was said."""
    options = question["options"]
    text = str(said).strip()
    # RULE: an option is chosen by its number or its label
    # RULE: a number written as a superscript is refused with the options
    # isdigit alone takes '²', which int() cannot read; only plain 0-9 is a number here.
    if text.isascii() and text.isdigit() and 1 <= int(text) <= len(options):
        return options[int(text) - 1]["label"]
    return next((o["label"] for o in options if o["label"].casefold() == text.casefold()), said)


def body_for(question: dict[str, Any], choice: Any, own_words: str | None) -> dict[str, Any]:
    """The answer the page would have sent for this spoken choice, or an error."""
    if (choice is None) == (own_words is None):
        return {"error": "give either a choice from the options or the owner's own words, not both"}
    if own_words is not None:
        return {"answer": {"text": own_words}}
    said = [c for c in (choice if isinstance(choice, list) else [choice]) if str(c).strip()]
    # RULE: an empty pick is refused
    if not said:
        return {"error": NOTHING_PICKED}
    # RULE: a choice named twice counts once
    picked = list(dict.fromkeys(_label(question, c) for c in said))
    return {"answer": picked if len(picked) > 1 or question["multi"] else picked[0]}


def answer(
    book: prompts.Book, prompt_id: str, choice: Any, own_words: str | None, publish: Any = None
) -> tuple[int, dict[str, Any]]:
    """Answer a waiting question by voice: the status, and what to tell the owner."""
    prompt = book.items.get(prompt_id)
    # RULE: a question no hook waits on is refused as on the screen
    if prompt is None or prompt["answer"] is not None:
        return 410, {"answered": False, "status": 410, "say": ON_THE_SCREEN}
    if prompt["kind"] != "question":
        return 409, {"answered": False, "error": "this is a tool call to approve; use approve or deny"}
    if not book.hook_waits(prompt):
        return 410, {"answered": False, "status": 410, "say": ON_THE_SCREEN}
    body = body_for(prompt["question"], choice, own_words)
    # RULE: a spoken choice that is not an option is refused with the options
    reason = body.get("error") or fits(prompt, body)
    if reason:
        return 409, {"answered": False, "error": reason, "options": numbered(prompt["question"])}
    settle(prompt, body, "voice", publish or book.publish)
    return 200, {"answered": True, "answer": body["answer"]}


def news(prompt: dict[str, Any], live: list[dict[str, Any]]) -> tuple[str, str, str] | None:
    """The news item for a question or an approval a hook has started waiting on, or None."""
    if prompt.get("kind") not in ("question", "approval"):
        return None
    name = next((str(r["name"]) for r in live if str(r.get("sessionId")) == prompt["session"] and r.get("name")), "")
    who = name or "A session"
    # RULE: an approval is news once a hook waits on it
    if prompt["kind"] == "approval":
        return who, "needs_answer", f"{who} asks to run {prompt.get('tool') or 'a tool'}"
    return who, "needs_answer", f"{who} asks: {prompt['question']['text']}"


def waiting_on(row: dict[str, Any], book: prompts.Book, projects_dir: Path) -> dict[str, Any] | None:
    """The box a running session waits on, from its live record: a question or an approval, or None.

    A message sent to it is queued and gets in once the box is answered, so the voice is told
    what holds it up. A question a hook holds comes from the book; otherwise from the transcript.
    """
    waiting_for = row.get("waitingFor")
    # RULE: a message to a session waiting on a box says what it waits on
    if row.get("status") != "waiting" or waiting_for not in BOXES:
        return None
    sid = str(row.get("sessionId"))
    kind = "approval" if waiting_for in transcripts.PERMISSION_WAITS else "question"
    held = [p for p in book.items.values() if p["session"] == sid and p["kind"] == kind and p["answer"] is None]
    prompt = next((p for p in held if book.hook_waits(p)), None)
    prompt_id = prompt["id"] if prompt else None
    if kind == "approval":
        return {"kind": kind, "question": None, "options": [], "prompt_id": prompt_id}
    if prompt is not None:
        asked = prompt["question"]
    else:
        scanned = transcripts.open_question(sid, str(row.get("cwd")), projects_dir, waiting_for) or {}
        asked = {"text": scanned.get("text"), "options": [{"label": label} for label in scanned.get("options", [])]}
    return {"kind": kind, "question": asked["text"], "options": numbered(asked), "prompt_id": prompt_id}


def queued_behind(row: dict[str, Any] | None, book: prompts.Book, projects_dir: Path) -> dict[str, Any]:
    """What to add to a sent message's answer when its session waits on a box; else nothing."""
    box = waiting_on(row, book, projects_dir) if row else None
    if box is None:
        return {}
    return {"waiting_on": box, "say": QUEUED if box["kind"] == "question" else QUEUED_APPROVAL}
