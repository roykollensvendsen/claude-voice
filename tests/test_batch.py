"""The batch script's own judgement, with GitHub and git left out: which pull requests are
cleared, what the batch body copies, how a culprit is found, and what is printed to merge."""

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / "scripts"))

import batch

HEAD = "a" * 40
OTHER = "b" * 40
PATCH = "c" * 40


def stamp(by="quality-claude-voice", head=HEAD, patch=PATCH[:8], tier="low", verdict="approve", exceptions=()):
    return {
        "by": by,
        "verdict": verdict,
        "head": head,
        "patch_id": patch,
        "tier": tier,
        "at": "2026-11-23T20:00:00+01:00",
        "ratchet_exceptions": list(exceptions),
        "file": f"claude-voice-1.{by}.json",
    }


def member(number=1, head=HEAD, patch=PATCH, draft=False, green=True, stamps=None, yes=None):
    return batch.Member(
        number=number,
        head=head,
        patch_id=patch,
        draft=draft,
        ci_green=green,
        stamps=[stamp()] if stamps is None else stamps,
        yes=yes,
    )


BOTH = [stamp(tier="risky"), stamp(by="voice-architect", tier="risky")]
YES = {"source": "checked", "head": HEAD, "at": "2026-11-23T21:00:00+01:00", "uuid": "u-1"}
BUILT = "2026-11-23T22:00:00+01:00"


def check_line(item="#1", sha=HEAD, result="accepted", matched=True, uuid="u-1"):
    return {
        "at": "2026-11-23T21:00:00+01:00",
        "decision": {"item": item, "sha": sha, "action": "merge", "uuid": uuid},
        "uuid": uuid,
        "sha_matched": matched,
        "result": result,
    }


def test_a_low_pull_request_with_one_stamp_at_its_head_and_patch_is_cleared():
    assert batch.why_not(member(), BUILT) == []


def test_a_pull_request_whose_patch_moved_is_not_cleared():
    assert batch.why_not(member(patch="d" * 40), BUILT) == [
        "quality-claude-voice stamped patch cccccccc, and as applied it is dddddddd"
    ]


def test_a_draft_is_not_cleared():
    assert batch.why_not(member(draft=True), BUILT) == ["it is a draft"]


def test_a_pull_request_whose_own_checks_are_not_green_is_not_cleared():
    assert batch.why_not(member(green=False), BUILT) == ["its own checks are not green at its head"]


def test_a_stamp_at_another_head_does_not_count():
    assert batch.why_not(member(stamps=[stamp(head=OTHER)]), BUILT) == ["no approving stamp at its head"]


def test_a_stamp_that_asks_for_changes_does_not_count():
    assert batch.why_not(member(stamps=[stamp(verdict="changes")]), BUILT) == ["no approving stamp at its head"]


def test_a_risky_pull_request_needs_both_stamps():
    assert batch.why_not(member(stamps=[stamp(tier="risky")], yes=YES), BUILT) == [
        "risky, and voice-architect has not stamped its head"
    ]


def test_a_risky_pull_request_needs_the_owners_yes_recorded_before_the_build():
    assert batch.why_not(member(stamps=BOTH), BUILT) == ["risky, and the owner's yes for its head is not recorded"]
    late = {**YES, "at": "2026-11-23T23:00:00+01:00"}
    assert batch.why_not(member(stamps=BOTH, yes=late), BUILT) == [
        "risky, and the owner's yes for its head is not recorded"
    ]
    moved = {**YES, "head": OTHER}
    assert batch.why_not(member(stamps=BOTH, yes=moved), BUILT) == [
        "risky, and the owner's yes for its head is not recorded"
    ]
    assert batch.why_not(member(stamps=BOTH, yes=YES), BUILT) == []


def test_the_owners_yes_is_taken_only_from_an_accepted_check_of_this_pull_request_at_its_head():
    assert batch.checked_yes([check_line()], "claude-voice", 1, HEAD) == YES
    assert batch.checked_yes([check_line(item="claude-voice#1")], "claude-voice", 1, HEAD) == YES
    for line in (
        check_line(result="refused"),
        check_line(item="#11"),
        check_line(item="claude-voice-mobile#1"),
        check_line(sha=OTHER),
        check_line(matched=False),
    ):
        assert batch.checked_yes([line], "claude-voice", 1, HEAD) is None, line


def test_a_yes_the_manager_attests_is_kept_apart_and_must_name_the_head():
    attested = batch.attested(["1:" + HEAD])
    assert attested == {1: {"source": "manager-attested", "head": HEAD}}
    assert batch.why_not(member(stamps=BOTH, yes=attested[1]), BUILT) == []
    moved = batch.attested(["1:" + OTHER])[1]
    assert batch.why_not(member(stamps=BOTH, yes=moved), BUILT) == [
        "risky, and the owner's yes for its head is not recorded"
    ]


@pytest.mark.parametrize("flag", ["1", "1:" + HEAD[:12], "x:" + HEAD, "1:" + HEAD.upper(), ":" + HEAD])
def test_an_attested_yes_must_be_a_number_and_a_full_sha(flag):
    with pytest.raises(ValueError):
        batch.attested([flag])


def test_any_stamp_that_says_risky_makes_the_pull_request_risky():
    stamps = [stamp(tier="low"), stamp(by="voice-architect", tier="risky")]
    assert batch.why_not(member(stamps=stamps), BUILT) == ["risky, and the owner's yes for its head is not recorded"]


def test_only_exceptions_every_stamp_recorded_are_copied_tagged_with_their_pull_request():
    line = "Ratchet-Exception: coverage - the module it covered was deleted"
    late = "Ratchet-Exception: mutation - added after the stamps"
    stamps = [stamp(exceptions=[line, late]), stamp(by="voice-architect", exceptions=[line])]
    assert batch.exceptions([member(number=7, stamps=stamps)]) == [f"{line} (#7)"]


def test_a_stamp_that_does_not_record_its_exceptions_leaves_the_pull_request_out():
    no_field = {k: v for k, v in stamp().items() if k != "ratchet_exceptions"}
    assert batch.why_not(member(stamps=[no_field]), BUILT) == [
        "quality-claude-voice's stamp does not record its ratchet exceptions"
    ]


def test_the_batch_branch_is_named_after_the_day_and_never_reuses_a_name():
    assert batch.branch_name("2026-11-24", set()) == "integration/2026-11-24"
    taken = {"integration/2026-11-24", "integration/2026-11-24-2"}
    assert batch.branch_name("2026-11-24", taken) == "integration/2026-11-24-3"


def test_a_failing_prefix_is_run_again_before_a_culprit_is_named():
    # pull requests 2 and 5 disagree: every prefix that holds both fails
    calls = []

    def fails(prefix):
        calls.append(tuple(prefix))
        return 2 in prefix and 5 in prefix

    assert batch.culprit([1, 2, 3, 4, 5, 6, 7], fails) == 5
    failing = [c for c in calls if 2 in c and 5 in c]
    # the prefix that names the culprit was run twice, and gave the same answer
    assert calls.count((1, 2, 3, 4, 5)) == 2
    assert failing


def test_a_failure_that_does_not_come_back_names_no_culprit():
    seen = []

    def flaky(prefix):
        seen.append(tuple(prefix))
        return len(seen) == 1

    assert batch.culprit([1, 2, 3], flaky) is None


def test_the_merge_list_prints_one_rebase_line_per_pull_request_in_order_with_its_records():
    members = [member(number=4, stamps=BOTH, yes=YES), member(number=9, head=OTHER, stamps=[stamp(head=OTHER)])]
    lines = batch.merge_list(members, "claude-voice")
    commands = [line for line in lines if line.startswith("gh ")]
    assert commands == [
        f"gh pr merge 4 --repo claude-voice --rebase --match-head-commit {HEAD}",
        f"gh pr merge 9 --repo claude-voice --rebase --match-head-commit {OTHER}",
    ]
    text = "\n".join(lines)
    assert "claude-voice-1.voice-architect.json" in text
    assert "owner's yes: checked, u-1" in text
    attested = [member(number=4, stamps=BOTH, yes=batch.attested(["4:" + HEAD])[4])]
    assert "owner's yes: manager-attested" in "\n".join(batch.merge_list(attested, "claude-voice"))


def test_the_tree_check_reports_a_difference_and_passes_only_on_equal_trees():
    assert batch.tree_check("1" * 40, "1" * 40) == []
    assert batch.tree_check("1" * 40, "2" * 40) == [
        "main's tree is 1111111111111111111111111111111111111111,"
        " and the batch's is 2222222222222222222222222222222222222222: stop, and open an issue"
    ]


def test_the_batch_status_goes_only_on_recorded_member_heads_after_a_green_quality_at_the_batch_head():
    recorded = {4: HEAD, 9: OTHER}
    assert batch.status_targets(recorded, quality_head="f" * 40, batch_head="f" * 40, green=True) == [HEAD, OTHER]
    assert batch.status_targets(recorded, quality_head="f" * 40, batch_head="f" * 40, green=False) == []
    assert batch.status_targets(recorded, quality_head="e" * 40, batch_head="f" * 40, green=True) == []


def test_a_merge_that_fails_part_way_stops_and_names_the_prefix_that_landed():
    report = batch.after_failed_merge([4, 9, 12], failed_at=9)
    assert report == [
        "merged: #4",
        "failed: #9; nothing after it was merged",
        "main now holds a prefix that was never tested as such: wait for main's own run there, and tell the manager",
    ]


@pytest.mark.parametrize("bad", ["", "abc", "A" * 40, "a" * 39])
def test_a_head_must_be_a_full_sha(bad):
    with pytest.raises(ValueError):
        batch.full_sha(bad)
