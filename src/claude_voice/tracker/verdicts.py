"""The reviewers' verdict files, read strictly for the board sync.

Each reviewer keeps one file per pull request in the bridge's state folder:
`reviews/<repo>-<number>.<name>.json`. The board sync reads a file only when it is a plain
file under 64 KB, holds a JSON object, and has a known verdict and a 40-character head.
Anything else is ignored and counted. Design: board-flow-v1 (a), with quality's c1, c1a, c2
and c6.

Who stamped it is the file's `by`, never its name. It counts only when `by` is on the
allow-list and the name's suffix is one that reviewer writes. Two files with the same `by`
count once, which the sync sees by keeping one stamp per reviewer.

The file's `repo` and `pr` must name the item, when they are there. `repo` comes as
`owner/repo`, as the bare name, or not at all. When it has an owner, that must be the board's
owner. A file without them falls back to its name. In every case the head is what ties the
file to the pull request: the sync counts only a head equal to the pull request's own.

A reviewer starting a review claims it with `claim`. That changes its own file in place, so
its notes and follow-ups stay.
"""

from __future__ import annotations

import json
import re
import stat
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .. import safefile

#: A verdict file's name, and nothing looser: <repo>-<number>.<reviewer>.json.
NAME = re.compile(r"^(?P<repo>[A-Za-z0-9._-]+)-(?P<number>[0-9]+)\.(?P<suffix>[a-z0-9-]+)\.json$")
#: The reviewers whose stamps count, and the name suffixes each writes under.
REVIEWERS = {
    "quality-claude-voice": {"quality"},
    "quality-2": {"quality"},
    "voice-architect": {"voice-architect", "architect"},
    "claude-voice-mobile-architect": {"claude-voice-mobile-architect"},
}
VERDICTS = {"reviewing", "approve", "changes"}
#: Spellings in use that mean one of the verdicts above.
SAME_AS = {"changes_requested": "changes"}
TIERS = {"low", "risky"}
HEAD = re.compile(r"[0-9a-f]{40}")
MAX_BYTES = 64 * 1024


class NotPlain(ValueError):
    """A verdict file that is a link or not a file; a claim never writes it."""


@dataclass(frozen=True)
class Verdict:
    by: str
    verdict: str
    head: str
    tier: str | None
    at: float  # when it was written: its own `at`, or else the file's time


@dataclass
class Reading:
    files: dict[tuple[str, int], list[Verdict]] = field(default_factory=dict)
    ignored: int = 0


def read(reviews: Path, owner: str, repos: set[str]) -> Reading:
    """The verdicts for pull requests in `repos` (bare names), by (repo, number)."""
    out = Reading()
    for path in sorted(reviews.iterdir()) if reviews.is_dir() else ():
        match = NAME.match(path.name)
        if not match or match["repo"] not in repos:
            continue
        verdict = _verdict(path, match, owner)
        if verdict is None:
            out.ignored += 1
            continue
        out.files.setdefault((match["repo"], int(match["number"])), []).append(verdict)
    return out


def _body(path: Path) -> dict[str, Any] | None:
    """The JSON object in a plain file under the size limit, or None."""
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_size >= MAX_BYTES:
        return None
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return body if isinstance(body, dict) else None


def _verdict(path: Path, match: re.Match[str], owner: str) -> Verdict | None:
    body = _body(path)
    if body is None:
        return None
    by = str(body.get("by") or "")
    # RULE: a stamp counts only from a reviewer on the allow list
    if match["suffix"] not in REVIEWERS.get(by, ()):
        return None
    # RULE: a verdict file naming a foreign owner is ignored
    if not _names_item(body, match, owner):
        return None
    verdict = SAME_AS.get(str(body.get("verdict")), str(body.get("verdict")))
    head = body.get("head")
    if verdict not in VERDICTS or not isinstance(head, str) or not HEAD.fullmatch(head):
        return None
    tier = body.get("tier") if body.get("tier") in TIERS else None
    return Verdict(by, verdict, head, tier, _when(body.get("at"), path))


def _names_item(body: dict[str, Any], match: re.Match[str], owner: str) -> bool:
    """Whether the file's own `repo` and `pr`, where present, name the item its name does."""
    repo = body.get("repo")
    if repo is not None:
        who, _, name = str(repo).rpartition("/")
        if name != match["repo"] or (who and who != owner):
            return False
    pr = body.get("pr")
    return pr is None or str(pr) == match["number"]


def _when(at: Any, path: Path) -> float:
    try:
        return datetime.fromisoformat(str(at)).timestamp()
    except ValueError:
        return path.lstat().st_mtime


def claimable(name: str, by: str, head: str) -> re.Match[str] | None:
    """The verdict file name `by` may claim a review of `head` in, or None when it may not."""
    match = NAME.fullmatch(name)
    # RULE: a claim is refused for another reviewers file or a short head
    if not match or match["suffix"] not in REVIEWERS.get(by, ()) or not HEAD.fullmatch(head):
        return None
    return match


def claim(path: Path, by: str, head: str, at: str) -> bool:
    """Mark `by`'s review of `head` as started, in its own file. False when it has already
    approved that head, so nothing changed."""
    # RULE: a claim never writes through a link
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise NotPlain(f"{path.name} is not a plain file; nothing was written")
    body = _body(path) if path.exists() else {"by": by}
    # RULE: a claim never overwrites a file it cannot read or another reviewers
    if body is None or body.get("by", by) != by:
        raise NotPlain(f"{path.name} is not a verdict file of {by}'s that can be read; nothing was written")
    # RULE: a reviewer who approved this head does not claim it again
    if body.get("verdict") == "approve" and body.get("head") == head:
        return False
    body.update(by=by, verdict="reviewing", head=head, at=at)
    safefile.replace(path, json.dumps(body, indent=2) + "\n")
    return True
