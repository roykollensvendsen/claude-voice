"""A session's choice box, read out and answered by voice while the phone hook waits on it.

The phone hook opens a prompt in the book and waits on it; an answer reaches the
session through that wait and no other way. So the voice can answer only while a
hook waits. Once it has stepped aside, the box is on the screen, and the voice says
so. A spoken choice is an option's number or label, several of them, or the
owner's own words, and it must fit the question as the page's answers must.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from . import prompts

log = logging.getLogger("claude_voice.answers")

ON_THE_SCREEN = (
    "That question is not waiting for the voice any more. If it is still open, answer it on the computer's screen."
)
NOTHING_PICKED = "nothing was picked: give an option's number or label, or the owner's own words"


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


def _label(question: dict[str, Any], said: Any) -> Any:
    """The option a spoken choice names, by number or by label in any case; else what was said."""
    options = question["options"]
    text = str(said).strip()
    # RULE: an option is chosen by its number or its label
    if text.isdigit() and 1 <= int(text) <= len(options):
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
    """The news item for a question a hook has started waiting on, or None for anything else."""
    if prompt.get("kind") != "question":
        return None
    name = next((str(r["name"]) for r in live if str(r.get("sessionId")) == prompt["session"] and r.get("name")), "")
    who = name or "A session"
    return who, "needs_answer", f"{who} asks: {prompt['question']['text']}"
