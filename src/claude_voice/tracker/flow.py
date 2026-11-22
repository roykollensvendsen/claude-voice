"""Which column a piece of work belongs in, and when it has gone quiet. Design: board-flow-v1.

Each function works from facts the sync read in this run: the work itself, the column it
sits in now (a kind such as "todo", or None), and the reviewers' verdicts. The columns are
named by kind; the board's own names are a setting.

The board never gates. Ready to merge and Waiting on the owner are labels, not permission:
whoever merges checks the verdict files and the checks at the full head again. A Stalled
flag is a hint for the manager's stall check, which keeps its own finding.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from .verdicts import Verdict

#: The checks' summed state that counts as green; anything else finished is a failure.
GREEN = {"SUCCESS", "SKIPPED", "NEUTRAL"}
PENDING = {"PENDING", "EXPECTED"}
#: How long work may go quiet before it is flagged Stalled, in seconds.
QUIET_PR = 30 * 60
QUIET_ISSUE = 120 * 60
QUIET_REVIEW = 120 * 60


def checks(work: dict[str, Any]) -> str:
    """The checks of the pull request's own head: "green", "failed" or "pending"."""
    nodes = (work.get("commits") or {}).get("nodes") or []
    commit = (nodes[-1] if nodes else {}).get("commit") or {}
    state = (commit.get("statusCheckRollup") or {}).get("state")
    # RULE: only the checks of the current head count
    if commit.get("oid") != work.get("headRefOid") or state is None or state in PENDING:
        return "pending"
    return "green" if state in GREEN else "failed"


def tier(stamps: Sequence[Verdict]) -> str:
    """risky when any file says so, and when none names a tier; low only when one says low."""
    tiers = {v.tier for v in stamps}
    return "low" if "low" in tiers and "risky" not in tiers else "risky"


def pr_column(work: dict[str, Any], now: str | None, stamps: Sequence[Verdict], owner_merges: Sequence[str]) -> str:
    """The column of a pull request: the first rule of board-flow-v1's table that matches."""
    if work["state"] != "OPEN":
        return "done"
    # RULE: a pull request in blocked stays there
    if now == "blocked":
        return "blocked"
    if work["isDraft"]:
        return "progress"
    return _reviewed(work, stamps, owner_merges)


def _reviewed(work: dict[str, Any], stamps: Sequence[Verdict], owner_merges: Sequence[str]) -> str:
    """Where an open, ready pull request stands in review, from the verdicts at its head."""
    head = [v for v in stamps if v.head == work.get("headRefOid")]
    # RULE: two files with the same reviewer count once
    approved = {v.by for v in head if v.verdict == "approve"}
    stamped = len(approved) >= (1 if tier(stamps) == "low" else 2)
    state = checks(work)
    if any(v.verdict == "changes" for v in head) or (stamped and state == "failed"):
        return "changes"
    if stamped and state == "green":
        return "waiting" if tier(stamps) in owner_merges else "ready"
    # RULE: every stamp in with checks pending is being reviewed
    if head:
        return "reviewing"
    return "review"


def issue_column(work: dict[str, Any], now: str | None, owner: str) -> str:
    """The column of an issue: the first rule of board-flow-v1's table that matches."""
    if work["state"] != "OPEN":
        return "done"
    held = now == "blocked"  # a hand move, kept until Done or moved by hand
    if held:
        return "blocked"
    # RULE: an open issue someone has taken is in progress
    if (work.get("assignees") or {}).get("totalCount"):
        return "progress"
    author = (work.get("author") or {}).get("login")
    # RULE: an unassigned issue that was in progress goes back to to do
    if now in ("todo", "progress"):
        return "todo"
    answered = any((c.get("author") or {}).get("login") == owner for c in work["comments"]["nodes"])
    if author != owner and not work["labels"]["totalCount"] and not answered:
        return "waiting"
    # RULE: an issue moved to waiting by hand stays there
    # The sync cannot tell an issue the manager parked from one it put there itself.
    if now in ("backlog", "waiting"):
        return now
    return "backlog"


def _age(work: dict[str, Any], clock: float) -> float:
    return clock - datetime.fromisoformat(str(work["updatedAt"]).replace("Z", "+00:00")).timestamp()


def stalled(work: dict[str, Any], kind: str, stamps: Sequence[Verdict], reason: str, clock: float) -> bool:
    """Whether work in column `kind` has gone quiet, as of `clock` (seconds)."""
    # RULE: blocked with an empty reason is stalled at once
    if kind == "blocked":
        return not reason.strip()
    if kind == "progress" and work["__typename"] == "PullRequest":
        return _age(work, clock) > QUIET_PR
    if kind == "progress":
        linked = (work.get("closedByPullRequestsReferences") or {}).get("totalCount")
        return not linked and _age(work, clock) > QUIET_ISSUE
    # RULE: a review waiting on pending checks is not stalled
    if kind == "reviewing" and checks(work) != "pending":
        claims = [v for v in stamps if v.verdict == "reviewing" and v.head == work.get("headRefOid")]
        # RULE: a claimed review older than 120 minutes is stalled
        return any(clock - v.at > QUIET_REVIEW for v in claims)
    return False
