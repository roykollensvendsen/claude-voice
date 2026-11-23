#!/usr/bin/env python3
"""Approved pull requests, tested together once before they reach main (ADR-CV-023).

A batch is main with each cleared pull request's commits applied on top, in the order they
were cleared, on a branch named integration/<date>[-n]. Continuous integration runs the
quality job (the mutation run and the ratchet) once there, rather than on each pull request.

This module holds the judgement, with GitHub and git kept out, so each part can be tested on
its own:

- `why_not`: why a pull request may not go into a batch, or nothing when it may;
- `exceptions`: the Ratchet-Exception lines the batch body copies;
- `culprit`: the pull request that breaks a batch, found by merge order;
- `merge_list`: the commands the manager runs once a batch is green, and the records each
  one rests on. This script never merges;
- `tree_check` and `after_failed_merge`: what to do once the merges ran;
- `status_targets`: the heads that get the `batch` status main requires.

The verdict files and the owner's yes records are plain files on this machine. They are read
as the reviewers' and the manager's word, as every reviewer reads them today. They guard
against slips and habits, not against a session that means harm.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

FULL_SHA = re.compile(r"[0-9a-f]{40}")
#: The reviewers a risky pull request needs, by the name each signs its verdicts with.
RISKY_NEEDS = ("quality-claude-voice", "voice-architect")
#: The shortest patch-id a stamp may record and still be compared.
SHORT = 8
BATCH = "integration/"


def full_sha(value: str) -> str:
    """`value`, when it is a full 40-character lowercase sha."""
    if not FULL_SHA.fullmatch(value):
        raise ValueError(f"not a full sha: {value!r}")
    return value


@dataclass
class Member:
    """One pull request as the batch sees it."""

    number: int
    head: str
    patch_id: str  # as applied onto the batch so far, in full
    draft: bool
    ci_green: bool
    stamps: list[dict[str, Any]] = field(default_factory=list)
    yes: dict[str, Any] | None = None


def _approving(m: Member) -> list[dict[str, Any]]:
    return [s for s in m.stamps if s.get("verdict") == "approve" and s.get("head") == m.head]


def _risky(m: Member) -> bool:
    # RULE: any stamp that says risky makes the pull request risky
    return any(s.get("tier") == "risky" for s in m.stamps)


def _when(at: Any) -> float | None:
    try:
        return datetime.fromisoformat(str(at)).timestamp()
    except ValueError:
        return None


def _yes_holds(m: Member, built: str) -> bool:
    yes = m.yes or {}
    # RULE: a risky pull request needs the owners yes recorded before the build
    if yes.get("head") != m.head:
        return False
    if yes.get("source") == "manager-attested":
        return True
    at, start = _when(yes.get("at")), _when(built)
    return yes.get("source") == "checked" and at is not None and start is not None and at < start


def why_not(m: Member, built: str) -> list[str]:
    """Why `m` may not go into a batch built at `built`; empty when it is cleared."""
    # RULE: a draft is not cleared
    if m.draft:
        return ["it is a draft"]
    # RULE: a pull request whose own checks are not green is not cleared
    if not m.ci_green:
        return ["its own checks are not green at its head"]
    stamps = _approving(m)
    # RULE: a stamp at another head does not count
    if not stamps:
        return ["no approving stamp at its head"]
    found = []
    for s in stamps:
        recorded = str(s.get("patch_id") or "")
        # RULE: a pull request whose patch moved is not cleared
        if len(recorded) < SHORT or not m.patch_id.startswith(recorded):
            found.append(f"{s.get('by')} stamped patch {recorded[:SHORT]}, and as applied it is {m.patch_id[:SHORT]}")
        # RULE: a stamp that does not record its exceptions leaves the pull request out
        if not isinstance(s.get("ratchet_exceptions"), list):
            found.append(f"{s.get('by')}'s stamp does not record its ratchet exceptions")
    if _risky(m):
        by = {str(s.get("by")) for s in stamps}
        # RULE: a risky pull request needs both stamps
        found += [f"risky, and {who} has not stamped its head" for who in RISKY_NEEDS if who not in by]
        if not _yes_holds(m, built):
            found.append("risky, and the owner's yes for its head is not recorded")
    return found


def checked_yes(rows: Iterable[dict[str, Any]], repo: str, number: int, head: str) -> dict[str, Any] | None:
    """The owner's yes for pull request `number` at `head`, from the decision checks' log.

    Only an accepted check counts, whose decision names this pull request and whose sha is
    `head` and was matched when it was checked.
    """
    names = {f"#{number}", f"{repo}#{number}"}
    for row in rows:
        decision = row.get("decision") or {}
        # RULE: the owners yes is taken only from an accepted check of this pull request at its head
        if row.get("result") != "accepted" or row.get("sha_matched") is not True:
            continue
        if str(decision.get("item")) in names and decision.get("sha") == head:
            return {"source": "checked", "head": head, "at": row.get("at"), "uuid": row.get("uuid")}
    return None


ATTESTED = re.compile(r"(?P<number>[1-9][0-9]*):(?P<sha>[0-9a-f]{40})")


def attested(flags: Iterable[str]) -> dict[int, dict[str, Any]]:
    """The owner's yes the manager attests to, from repeated --owner-yes N:sha flags."""
    out = {}
    for flag in flags:
        match = ATTESTED.fullmatch(flag)
        # RULE: an attested yes must be a number and a full sha
        if not match:
            raise ValueError(f"--owner-yes takes N:<40-character sha>, not {flag!r}")
        out[int(match["number"])] = {"source": "manager-attested", "head": match["sha"]}
    return out


def exceptions(members: Iterable[Member]) -> list[str]:
    """The Ratchet-Exception lines every approving stamp of a pull request recorded, tagged."""
    out = []
    for m in members:
        recorded = [s.get("ratchet_exceptions") for s in _approving(m)]
        if not recorded or not all(isinstance(r, list) for r in recorded):
            continue
        # RULE: only exceptions every stamp recorded are copied tagged with their pull request
        shared = set.intersection(*(set(map(str, r)) for r in recorded if isinstance(r, list)))
        out += [f"{line} (#{m.number})" for line in sorted(shared)]
    return out


def branch_name(day: str, taken: set[str]) -> str:
    """integration/<day>, or with -2, -3 ... when that name was used before."""
    name, n = f"{BATCH}{day}", 1
    # RULE: the batch branch is named after the day and never reuses a name
    while name in taken:
        n += 1
        name = f"{BATCH}{day}-{n}"
    return name


def culprit(order: Sequence[int], fails: Callable[[Sequence[int]], bool]) -> int | None:
    """The pull request whose prefix first fails, found by halving; None when nothing is to blame.

    `fails(prefix)` runs the batch made of that prefix of `order`. The whole batch has already
    failed once, in its own run. The prefix that names the culprit is run once more before it
    names anyone, so one flaky run never blames a pull request.
    """
    low, high = 0, len(order)  # the shortest failing prefix is longer than low, at most high
    while high - low > 1:
        mid = (low + high) // 2
        if fails(order[:mid]):
            high = mid
        else:
            low = mid
    # RULE: a failing prefix is run again before a culprit is named
    if not fails(order[:high]):
        return None
    return order[high - 1]


def merge_list(members: Sequence[Member], repo: str) -> list[str]:
    """One merge command per pull request, in batch order, each with the records it rests on."""
    lines = []
    for m in members:
        # RULE: the merge list prints one rebase line per pull request in order with its records
        lines.append(f"gh pr merge {m.number} --repo {repo} --rebase --match-head-commit {full_sha(m.head)}")
        for s in _approving(m):
            lines.append(f"  stamp: {s.get('by')}, head {s.get('head')}, patch {s.get('patch_id')}, {s.get('file')}")
        if m.yes:
            source = m.yes.get("source")
            detail = f", {m.yes.get('uuid')}" if source == "checked" else ""
            lines.append(f"  owner's yes: {source}{detail}")
    return lines


def tree_check(main_tree: str, batch_tree: str) -> list[str]:
    """Nothing when main's tree is the one the batch proved; otherwise what to do."""
    # RULE: the tree check reports a difference and passes only on equal trees
    if main_tree != batch_tree:
        return [f"main's tree is {main_tree}, and the batch's is {batch_tree}: stop, and open an issue"]
    return []


def after_failed_merge(order: Sequence[int], failed_at: int) -> list[str]:
    """What to report when the merge of `failed_at` failed part-way through `order`."""
    # RULE: a merge that fails part way stops and names the prefix that landed
    landed = list(order[: order.index(failed_at)])
    return [
        f"merged: {', '.join(f'#{n}' for n in landed) or 'nothing'}",
        f"failed: #{failed_at}; nothing after it was merged",
        "main now holds a prefix that was never tested as such: wait for main's own run there, and tell the manager",
    ]


def status_targets(recorded: dict[int, str], quality_head: str, batch_head: str, green: bool) -> list[str]:
    """The heads that get the `batch` status: every recorded member head, and only after a
    green quality run at the batch head as it is now."""
    # RULE: the batch status goes only on recorded member heads after a green quality at the batch head
    if not green or quality_head != batch_head:
        return []
    return [full_sha(head) for _, head in sorted(recorded.items())]
