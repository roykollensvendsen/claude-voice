"""Which column a piece of work belongs in, and when it has gone quiet. Design: board-flow-v1."""

from __future__ import annotations

import pytest

from claude_voice.tracker import flow
from claude_voice.tracker.verdicts import Verdict

OWNER = "example-owner"
HEAD = "a" * 40
OLD = "b" * 40
NOW = 1_800_000_000.0
MINUTE = 60.0


def iso(seconds_ago):
    from datetime import UTC, datetime

    return datetime.fromtimestamp(NOW - seconds_ago, UTC).isoformat().replace("+00:00", "Z")


def pr(*, state="OPEN", draft=False, checks="SUCCESS", head=HEAD, checked=HEAD, quiet=0):
    rollup = None if checks is None else {"state": checks}
    return {
        "__typename": "PullRequest",
        "state": state,
        "isDraft": draft,
        "headRefOid": head,
        "updatedAt": iso(quiet),
        "commits": {"nodes": [{"commit": {"oid": checked, "statusCheckRollup": rollup}}]},
    }


def issue(*, state="OPEN", author=OWNER, labels=0, named=(), commenters=(), assignees=0, linked=0, quiet=0):
    return {
        "__typename": "Issue",
        "state": state,
        "author": {"login": author},
        "labels": {"totalCount": labels + len(named), "nodes": [{"name": n} for n in named]},
        "comments": {"nodes": [{"author": {"login": who}} for who in commenters]},
        "assignees": {"totalCount": assignees},
        "closedByPullRequestsReferences": {"totalCount": linked},
        "updatedAt": iso(quiet),
    }


def stamp(by="quality-claude-voice", verdict="approve", head=HEAD, tier=None, ago=0):
    return Verdict(by, verdict, head, tier, NOW - ago)


def where(work, now=None, stamps=(), owner_merges=("risky",)):
    if work["__typename"] == "PullRequest":
        return flow.pr_column(work, now, list(stamps), owner_merges)
    return flow.issue_column(work, now, OWNER)


LOW = stamp(tier="low")
TWO = [stamp(tier="risky"), stamp(by="voice-architect")]

# --- Pull requests: one test per row of the table ---------------------------------------


@pytest.mark.parametrize("state", ["MERGED", "CLOSED"])
def test_pr_row_1_merged_or_closed_is_done(state):
    assert where(pr(state=state), now="blocked") == "done"


def test_pr_row_2_a_draft_is_in_progress():
    assert where(pr(draft=True), now="reviewing", stamps=TWO) == "progress"


def test_a_pull_request_in_blocked_stays_there():
    assert where(pr(), now="blocked", stamps=TWO) == "blocked"


def test_pr_row_4_a_changes_verdict_at_the_head_is_changes_requested():
    assert where(pr(), stamps=[stamp(verdict="changes"), stamp(by="voice-architect")]) == "changes"


def test_pr_row_4_two_approvals_and_red_checks_is_changes_requested():
    assert where(pr(checks="FAILURE"), stamps=TWO) == "changes"
    assert where(pr(checks="ERROR"), stamps=TWO) == "changes"


def test_pr_row_5_one_approval_green_checks_low_is_ready_to_merge():
    assert where(pr(), stamps=[LOW]) == "ready"


def test_pr_row_5_two_approvals_green_checks_risky_waits_on_the_owner():
    assert where(pr(), stamps=TWO) == "waiting"
    assert where(pr(), stamps=TWO, owner_merges=()) == "ready"


def test_pr_row_6_reviewing_is_being_reviewed():
    assert where(pr(), stamps=[stamp(verdict="reviewing")]) == "reviewing"


def test_pr_row_6_a_risky_pull_request_with_one_approval_is_being_reviewed():
    assert where(pr(), stamps=[stamp(tier="risky")]) == "reviewing"


def test_pr_row_7_a_verdict_at_an_older_head_is_waiting_for_review():
    assert where(pr(), stamps=[stamp(head=OLD, tier="low")]) == "review"
    assert where(pr()) == "review"


# --- Quality's conditions on the pull request rules -------------------------------------


def test_two_files_with_the_same_reviewer_count_once():
    """c1: two stamps are two different reviewers."""
    assert where(pr(), stamps=[stamp(tier="risky"), stamp(tier="risky")]) == "reviewing"


def test_every_stamp_in_with_checks_pending_is_being_reviewed():
    """c3: not Waiting for review, which would say nobody has looked."""
    assert where(pr(checks="PENDING"), stamps=TWO) == "reviewing"
    assert where(pr(checks=None), stamps=TWO) == "reviewing"
    assert where(pr(checked=OLD), stamps=TWO) == "reviewing"  # the checks are not of this head


def test_only_the_checks_of_the_current_head_count():
    """Checks of an older commit say nothing about the head, green or red."""
    assert flow.checks(pr(checked=OLD)) == "pending"
    assert flow.checks(pr(checks="FAILURE", checked=OLD)) == "pending"
    assert flow.checks(pr()) == "green"


@pytest.mark.parametrize("state", ["SUCCESS", "SKIPPED", "NEUTRAL"])
def test_green_means_success_skipped_or_neutral(state):
    """c3: anything else that has finished is a failure."""
    assert where(pr(checks=state), stamps=[LOW]) == "ready"


def test_a_pull_request_counts_as_risky_unless_a_file_says_low():
    assert where(pr(), stamps=[stamp()]) == "reviewing"  # no tier named
    assert where(pr(), stamps=[LOW, stamp(by="voice-architect", tier="risky")]) == "waiting"


# --- Issues: one test per row of the table ----------------------------------------------


def test_issue_row_1_closed_is_done():
    assert where(issue(state="CLOSED"), now="todo") == "done"


def test_issue_row_2_blocked_and_to_do_stay():
    assert where(issue(author="someone-else"), now="blocked") == "blocked"
    assert where(issue(assignees=1), now="blocked") == "blocked"
    assert where(issue(author="someone-else"), now="todo") == "todo"


def test_an_open_issue_someone_has_taken_is_in_progress():
    assert where(issue(assignees=1), now="todo") == "progress"
    assert where(issue(assignees=1)) == "progress"


def test_issue_row_4_an_outside_issue_nobody_has_triaged_waits_on_the_owner():
    assert where(issue(author="someone-else")) == "waiting"


def test_issue_row_5_anything_else_stays_where_it_was_put_or_goes_to_backlog():
    assert where(issue(), now="backlog") == "backlog"
    assert where(issue()) == "backlog"
    assert where(issue(), now="ready") == "backlog"  # a column the sync owns is put right


def test_an_unassigned_issue_that_was_in_progress_goes_back_to_to_do():
    assert where(issue(), now="progress") == "todo"


def test_a_triaged_outside_issue_goes_to_backlog_unless_it_is_in_waiting():
    assert where(issue(author="someone-else", labels=1)) == "backlog"
    assert where(issue(author="someone-else", commenters=[OWNER]), now="ready") == "backlog"


def test_only_a_parked_issue_stays_in_waiting_once_the_owner_has_answered_it():
    """An outside issue the owner has labelled or answered no longer waits on the owner (#477).
    The manager keeps a decision there with the parked label, whoever opened the issue."""
    assert where(issue(author="someone-else", labels=1), now="waiting") == "backlog"
    assert where(issue(author="someone-else", commenters=[OWNER]), now="waiting") == "backlog"
    assert where(issue(), now="waiting") == "backlog"
    assert where(issue(named=["parked"]), now="waiting") == "waiting"
    assert where(issue(author="someone-else", named=["parked"], commenters=[OWNER])) == "waiting"
    assert where(issue(named=["bug"]), now="waiting") == "backlog"


# --- Stalled ----------------------------------------------------------------------------


def stalled(work, kind, stamps=(), reason="x"):
    return flow.stalled(work, kind, list(stamps), reason, NOW)


def test_a_pull_request_in_progress_quiet_for_30_minutes_is_stalled():
    assert stalled(pr(draft=True, quiet=31 * MINUTE), "progress")
    assert not stalled(pr(draft=True, quiet=29 * MINUTE), "progress")


def test_an_assigned_issue_with_no_pull_request_quiet_for_120_minutes_is_stalled():
    assert stalled(issue(assignees=1, quiet=121 * MINUTE), "progress")
    assert not stalled(issue(assignees=1, quiet=119 * MINUTE), "progress")
    assert not stalled(issue(assignees=1, linked=1, quiet=500 * MINUTE), "progress")


def test_a_claimed_review_older_than_120_minutes_is_stalled():
    """c5: a claimed review cannot hang unseen."""
    assert stalled(pr(), "reviewing", [stamp(verdict="reviewing", ago=121 * MINUTE)])
    assert not stalled(pr(), "reviewing", [stamp(verdict="reviewing", ago=60 * MINUTE)])
    assert not stalled(pr(), "reviewing", [stamp(verdict="reviewing", head=OLD, ago=500 * MINUTE)])


def test_blocked_with_an_empty_reason_is_stalled_at_once():
    assert stalled(issue(), "blocked", reason="  ")
    assert not stalled(issue(), "blocked", reason="waits on #439 merge")


def test_other_columns_are_never_stalled():
    assert not stalled(pr(quiet=900 * MINUTE), "review")
    assert not stalled(issue(quiet=900 * MINUTE), "backlog")


def test_a_draft_in_blocked_stays_there():
    """A stuck builder's pull request is usually a draft, so Blocked is kept ahead of the draft row."""
    assert where(pr(draft=True), now="blocked") == "blocked"


def test_a_review_waiting_on_pending_checks_is_not_stalled():
    """c5: a reviewer waiting on checks that are still running is not stuck."""
    assert not stalled(pr(checks="PENDING"), "reviewing", [stamp(verdict="reviewing", ago=500 * MINUTE)])
