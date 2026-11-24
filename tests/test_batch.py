"""The batch script's own judgement, with GitHub and git left out: which pull requests are
cleared, what the batch body copies, how a culprit is found, and what is printed to merge."""

import json
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
    assert batch.why_not(member(stamps=[stamp(verdict="changes")]), BUILT) == [
        "no approving stamp at its head",
        "quality-claude-voice says changes at its head",
    ]


@pytest.mark.parametrize("verdict", ["changes", "reviewing", "something else"])
def test_a_stamp_at_its_head_that_does_not_approve_leaves_the_pull_request_out(verdict):
    stamps = [stamp(), stamp(by="voice-architect", verdict=verdict)]
    assert batch.why_not(member(stamps=stamps), BUILT) == [f"voice-architect says {verdict} at its head"]
    older = [stamp(), stamp(by="voice-architect", verdict=verdict, head=OTHER)]
    assert batch.why_not(member(stamps=older), BUILT) == []


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


@pytest.mark.parametrize("other", ["risky", None, "medium"])
def test_a_pull_request_is_low_only_when_every_stamp_says_low(other):
    stamps = [stamp(tier="low"), stamp(by="voice-architect", tier=other)]
    assert batch.why_not(member(stamps=stamps), BUILT) == ["risky, and the owner's yes for its head is not recorded"]
    one = [stamp(tier=other)]
    assert "risky, and voice-architect has not stamped its head" in batch.why_not(member(stamps=one), BUILT)


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


def test_a_culprit_is_named_only_when_the_prefix_before_it_passes_again():
    # the real culprit is 2, but the run of the first four passes once by chance
    flaked = []

    def fails(prefix):
        if len(prefix) == 4 and not flaked:
            flaked.append(True)
            return False
        return 2 in prefix

    assert batch.culprit([1, 2, 3, 4, 5, 6, 7, 8], fails) is None


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


# The command's own reading of GitHub and the stamp files, still without either.


def write_stamp(folder, name, **fields):
    body = {**stamp(), **fields}
    body.pop("file")
    (folder / name).write_text(json.dumps(body), encoding="utf-8")


def test_only_a_stamp_from_a_known_reviewer_under_its_own_file_name_counts(tmp_path):
    write_stamp(tmp_path, "claude-voice-7.quality.json", pr=7)
    write_stamp(tmp_path, "claude-voice-7.voice-architect.json", by="voice-architect", pr=7)
    write_stamp(tmp_path, "claude-voice-7.notes.json", by="someone-else", pr=7)
    write_stamp(tmp_path, "claude-voice-7.architect.json", by="quality-claude-voice", pr=7)
    write_stamp(tmp_path, "claude-voice-8.quality.json", pr=8)
    write_stamp(tmp_path, "claude-voice-7.quality-2.json", pr=8)
    stamps = batch.read_stamps(tmp_path, "claude-voice", 7)
    assert sorted((s["by"], s["file"]) for s in stamps) == [
        ("quality-claude-voice", "claude-voice-7.quality.json"),
        ("voice-architect", "claude-voice-7.voice-architect.json"),
    ]


def test_a_stamp_file_that_is_not_plain_json_is_skipped(tmp_path):
    (tmp_path / "claude-voice-7.quality.json").write_text("{not json", encoding="utf-8")
    (tmp_path / "claude-voice-7.voice-architect.json").symlink_to(tmp_path / "elsewhere.json")
    assert batch.read_stamps(tmp_path, "claude-voice", 7) == []


def check(name, conclusion="SUCCESS", status="COMPLETED", started="2026-11-23T20:00:00Z"):
    return {
        "__typename": "CheckRun",
        "name": name,
        "workflowName": "checks",
        "status": status,
        "conclusion": conclusion,
        "startedAt": started,
    }


LATER = "2026-11-23T21:00:00Z"


@pytest.mark.parametrize(
    ("rollup", "green"),
    [
        ([check("checks"), check("quality", "SKIPPED"), check("note", "NEUTRAL")], True),
        ([check("checks"), check("red-first", "FAILURE")], False),
        ([check("checks"), check("windows", None, "IN_PROGRESS")], False),
        ([check("checks"), check("checks (3.12)", "CANCELLED")], False),
        ([{"__typename": "StatusContext", "context": "batch", "state": "SUCCESS"}, check("checks")], True),
        ([{"__typename": "StatusContext", "context": "batch", "state": "PENDING"}], False),
        ([], False),
        ([check("red-first", "FAILURE"), check("red-first", started=LATER)], True),
        ([check("red-first"), check("red-first", "FAILURE", started=LATER)], False),
        ([check("windows"), check("windows", None, "IN_PROGRESS", started=LATER)], False),
        ([check("windows"), check("windows", None, "QUEUED", started=None)], False),
        ([check("windows", None, "IN_PROGRESS"), check("windows", started=LATER)], False),
        ([check("windows", "FAILURE"), check("windows", started=None)], True),
    ],
)
def test_own_checks_are_green_only_when_every_check_finished_and_none_failed(rollup, green):
    assert batch.ci_green(rollup) is green


@pytest.mark.parametrize("state", ["PENDING", "EXPECTED", "FAILURE"])
def test_a_pull_requests_own_checks_leave_out_the_batch_status(state):
    # once main requires `batch`, a member's checks list it as waiting until its batch is green
    waiting = {"__typename": "StatusContext", "context": "batch", "state": state}
    assert batch.ci_green([check("windows"), waiting]) is True
    assert batch.ci_green([waiting]) is False


def test_the_batch_keeps_the_order_the_pull_requests_were_cleared_in():
    late = member(1, stamps=[stamp(), {**stamp(by="voice-architect"), "at": "2026-11-23T23:00:00+01:00"}])
    early = member(2, stamps=[{**stamp(), "at": "2026-11-23T19:00:00+01:00"}])
    middle = member(3, stamps=[stamp()])
    stale = {**stamp(head=OTHER), "at": "2026-11-24T09:00:00+01:00"}
    middle_too = member(4, stamps=[stamp(), stale])
    assert [m.number for m in batch.cleared_order([late, early, middle, middle_too])] == [2, 3, 4, 1]


def recorded():
    return batch.Recorded(
        branch="integration/2026-11-24",
        base="d" * 40,
        members=[
            batch.Entry(4, HEAD, PATCH, "low", "e" * 40),
            batch.Entry(9, OTHER, "f" * 40, "risky", "1" * 40),
        ],
    )


def test_the_batch_body_records_each_member_and_reads_back_the_same():
    left_out = {12: ["it is a draft"]}
    body = batch.render_body(recorded(), ["Ratchet-Exception: coverage -0.1 (#9)"], left_out)
    assert "Do not merge" in body
    assert "#12: it is a draft" in body
    assert "Ratchet-Exception: coverage -0.1 (#9)" in body
    assert batch.parse_body(body) == recorded()


def test_a_body_without_its_record_reads_as_nothing():
    assert batch.parse_body("a hand-written body") is None
    assert batch.parse_body("<!-- batch-record\nbase x\n-->") is None
    assert batch.parse_body("<!-- batch-record\nbranch b\nbase x\nmember four a b c d\n-->") is None


def test_the_batch_body_carries_each_copied_exception_where_the_ratchet_reads_it():
    import ratchet

    copied = batch.exceptions([member(9, stamps=[stamp(exceptions=["Ratchet-Exception: coverage - a module went"])])])
    body = batch.render_body(recorded(), copied, {})
    assert ratchet.exceptions(body) == {"coverage"}


PINS = """\
Red-first: packages/x/tests/test_y.py, a new module.
Red-first: existing behaviour - test_the_sessions_are_in_one_list
  Red-first: existing behavior – test_a_number_is_never_passed_on
Not a marker: Red-first: existing behaviour - test_quoted
"""


def test_a_members_existing_behaviour_lines_are_copied_tagged_with_its_pull_request():
    found = batch.pinned([member(4), batch.Member(9, OTHER, PATCH, False, True, body=PINS)])
    assert found == [
        "Red-first: existing behaviour - test_the_sessions_are_in_one_list (#9)",
        "Red-first: existing behavior – test_a_number_is_never_passed_on (#9)",
    ]


def test_the_batch_body_carries_each_existing_behaviour_line_where_red_first_reads_it():
    import red_first

    lines = batch.pinned([batch.Member(9, OTHER, PATCH, False, True, body=PINS)])
    body = batch.render_body(recorded(), [], {}, lines)
    assert red_first.excuses(body) == {"test_the_sessions_are_in_one_list", "test_a_number_is_never_passed_on"}
    assert batch.parse_body(body) == recorded()


def test_the_merge_list_refuses_when_main_moved_off_the_base_or_a_member_is_no_longer_cleared():
    assert batch.merge_refusals("d" * 40, "d" * 40, {4: [], 9: []}) == []
    assert batch.merge_refusals("d" * 40, "0" * 40, {4: [], 9: []}) == [
        f"main is at {'0' * 40}, not the batch's base {'d' * 40}: build the batch again"
    ]
    assert batch.merge_refusals("d" * 40, "d" * 40, {4: [], 9: ["its own checks are not green at its head"]}) == [
        "#9 is no longer cleared: its own checks are not green at its head; nothing is merged"
    ]


def test_the_status_lines_name_the_batch_and_its_run():
    lines = batch.status_lines("o/r", [HEAD], "integration/2026-11-24", "https://run/1")
    assert lines == [
        f"gh api --method POST repos/o/r/statuses/{HEAD} -f state=success -f context=batch"
        " -f description=integration/2026-11-24 -f target_url=https://run/1"
    ]


def run(sha, status="completed", conclusion="success"):
    return {"headSha": sha, "status": status, "conclusion": conclusion, "url": f"https://run/{sha[:2]}"}


def test_verify_waits_for_a_complete_main_run_at_the_batch_tree_or_later():
    at_tree, later = "1" * 40, "2" * 40
    since = [later, at_tree]  # newest first, from main's tip back to the commit with the batch's tree
    assert batch.main_run(since, [run(later, "in_progress", ""), run(at_tree, "completed", "cancelled")]) is None
    failed = run(at_tree, "completed", "failure")
    assert batch.main_run(since, [run("9" * 40), failed]) == failed
    assert batch.main_run(since, [run(later), run(at_tree, "completed", "failure")]) == run(later)


def test_doctor_reports_when_main_does_not_require_batch_or_still_requires_quality():
    assert batch.protection_drift(["checks (3.12)", "batch"]) == []
    assert batch.protection_drift(["checks (3.12)", "quality"]) == [
        "main's protection does not require batch",
        "main's protection still requires quality",
    ]
