"""The lines `claude-voice tracker status --check` prints, each one fit for a public board.

Every line goes through the privacy gate before it is printed: the scan for
private data the friction log uses (`workflow.core.gate`), with the owner's own
private words. A line that holds any is refused, and the refusal names the item,
never the text. A repository that is not public is refused whole.

The owner's words are in a file outside every repository, `words = ["...", ...]`.
A missing file is said plainly, and only the patterns are checked. A file that is
there but cannot be read, that others may read, or that holds an empty word refuses
the check, so the scan never runs short unnoticed.
"""

from __future__ import annotations

import stat
import tomllib
from collections import Counter
from pathlib import Path

from claude_voice.restarts import one_line
from claude_voice.tracker.base import LABELS, STATES, Item
from claude_voice.workflow.core.gate import blank_tokens, private


class WordsError(Exception):
    """The owner's private word list is there but cannot be used."""


def private_words(path: Path) -> tuple[frozenset[str], str | None]:
    """The owner's private words, and a note to show when there is no list at all."""
    if not path.exists() and not path.is_symlink():
        return frozenset(), f"no private word list at {path}, so only the patterns are checked"
    try:
        mode = path.stat().st_mode
        # RULE: a private word list others may read refuses the check
        if mode & (stat.S_IRWXG | stat.S_IRWXO):
            raise WordsError(
                f"the private word list at {path} may be read by others. Make it yours alone: chmod 600 {path}"
            )
        words = tomllib.loads(path.read_text(encoding="utf-8"))["words"]
        if not isinstance(words, list) or not all(isinstance(w, str) for w in words):
            raise TypeError("words is not a list of text")
        # RULE: an empty private word refuses the check
        if any(not w.strip() for w in words):
            raise ValueError("a word is empty")
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError, KeyError, TypeError, ValueError) as exc:
        why = (
            f"the private word list at {path} cannot be read ({type(exc).__name__}), so nothing was checked. "
            'Make it a file holding words = ["..."], or move it away.'
        )
        # RULE: a broken private word list refuses the check
        raise WordsError(why) from exc
    return frozenset(words), None


def gate(text: str, words: frozenset[str]) -> str | None:
    """The line as a public board may show it, or None when it holds private data."""
    flat = blank_tokens(one_line(text))
    # RULE: a status line with private data is refused
    if private(flat, words):
        return None
    return flat


def lines(component: str, items: list[Item], words: frozenset[str], capped: int | None = None) -> tuple[list[str], int]:
    """A line per item, in the order the states are told, then a summary, and how many were refused.

    `capped` is the most the tracker reads in one go, given when it read that many, so a
    last line says that more may be open.
    """
    ordered = sorted(items, key=lambda i: (STATES.index(i.state), i.id))
    out, refused = [], 0
    for item in ordered:
        kept = gate(f"{item.id} {LABELS[item.state]}: {item.needs}", words)
        if kept is None:
            refused += 1
            kept = f"{item.id}: refused by the privacy gate, in its why"
        out.append(kept)
    counts = Counter(i.state for i in items)
    summary = (
        f"{component}: {len(items)} open pull request{'' if len(items) == 1 else 's'}: {counts['moving']} moving, "
        f"{counts['stopped']} stopped, {counts['waits_on_owner']} waiting on the owner"
        + (f", {refused} refused" if refused else "")
    )
    kept = gate(summary, words)
    out.append(kept or "the summary was refused by the privacy gate")
    if capped:
        out.append(f"only the first {capped} open pull requests were read; more may be open")
    return out, refused + (kept is None)
