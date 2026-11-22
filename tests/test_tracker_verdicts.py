"""The reviewers' verdict files, as the board sync reads them: strictly, and only what it needs."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from claude_voice.tracker import verdicts

OWNER = "example-owner"
HEAD = "a" * 40
OLD = "b" * 40


def write(reviews, name, **body):
    reviews.mkdir(exist_ok=True)
    path = reviews / name
    path.write_text(json.dumps(body))
    return path


def read(reviews):
    return verdicts.read(reviews, OWNER, {"demo"})


def stamp(by="quality-claude-voice", verdict="approve", head=HEAD, **more):
    return {"by": by, "verdict": verdict, "head": head, "repo": f"{OWNER}/demo", "pr": 12} | more


def test_a_plain_verdict_file_is_read(tmp_path):
    reviews = tmp_path / "reviews"
    write(reviews, "demo-12.quality.json", **stamp(tier="low"))
    found = read(reviews)
    [v] = found.files[("demo", 12)]
    assert (v.by, v.verdict, v.head, v.tier) == ("quality-claude-voice", "approve", HEAD, "low")
    assert found.ignored == 0


# c1: the reviewer is the file's `by`, on the allow-list, and the name's suffix is that reviewer's.


def test_a_stamp_counts_only_from_a_reviewer_on_the_allow_list(tmp_path):
    reviews = tmp_path / "reviews"
    write(reviews, "demo-12.anything.json", **stamp(by="anything"))
    write(reviews, "demo-12.quality.json", **stamp(by="voice-architect"))  # the name is quality's
    write(reviews, "demo-12.voice-architect.json", **stamp(by="voice-architect"))
    found = read(reviews)
    assert [v.by for v in found.files[("demo", 12)]] == ["voice-architect"]
    assert found.ignored == 2


def test_quality_2_writes_under_qualitys_name_and_counts_as_itself(tmp_path):
    reviews = tmp_path / "reviews"
    write(reviews, "demo-12.quality.json", **stamp(by="quality-2"))
    write(reviews, "demo-12.architect.json", **stamp(by="voice-architect"))
    assert sorted(v.by for v in read(reviews).files[("demo", 12)]) == ["quality-2", "voice-architect"]


def test_a_file_for_another_item_does_not_count(tmp_path):
    reviews = tmp_path / "reviews"
    write(reviews, "demo-12.quality.json", **stamp(pr=13))
    write(reviews, "demo-12.voice-architect.json", **stamp(by="voice-architect", repo=f"{OWNER}/other"))
    assert read(reviews).files == {}


# c1a: "repo" comes as owner/repo, as the bare name, or not at all.


@pytest.mark.parametrize(
    "fields",
    [{"repo": f"{OWNER}/demo", "pr": 12}, {"repo": "demo", "pr": "12"}, {}],
    ids=["owner-and-repo", "bare-name", "missing"],
)
def test_the_repo_in_a_verdict_file_comes_in_three_forms(fields, tmp_path):
    reviews = tmp_path / "reviews"
    body = {k: v for k, v in stamp().items() if k not in ("repo", "pr")} | fields
    write(reviews, "demo-12.quality.json", **body)
    assert [v.by for v in read(reviews).files[("demo", 12)]] == ["quality-claude-voice"]


def test_a_verdict_file_naming_a_foreign_owner_is_ignored(tmp_path):
    reviews = tmp_path / "reviews"
    write(reviews, "demo-12.quality.json", **stamp(repo="someone-else/demo"))
    found = read(reviews)
    assert found.files == {} and found.ignored == 1


# c2: the verdicts in use, and the stray spelling mapped rather than dropped.


def test_a_changes_requested_verdict_is_read_as_changes(tmp_path):
    reviews = tmp_path / "reviews"
    write(reviews, "demo-12.quality.json", **stamp(verdict="changes_requested"))
    write(reviews, "demo-12.voice-architect.json", **stamp(by="voice-architect", verdict="maybe"))
    found = read(reviews)
    assert [v.verdict for v in found.files[("demo", 12)]] == ["changes"]
    assert found.ignored == 1


# The safe read: anything else is ignored and counted.


def test_a_file_that_is_a_link_too_big_not_json_or_with_a_bad_head_is_ignored(tmp_path):
    reviews = tmp_path / "reviews"
    real = write(tmp_path, "real.json", **stamp())
    reviews.mkdir()
    (reviews / "demo-1.quality.json").symlink_to(real)
    write(reviews, "demo-2.quality.json", **stamp(notes="x" * verdicts.MAX_BYTES))
    (reviews / "demo-3.quality.json").write_text("not json")
    write(reviews, "demo-4.quality.json", **stamp(head="abc"))
    write(reviews, "demo-5.quality.json", **stamp(head=HEAD.upper()))
    found = verdicts.read(reviews, OWNER, {"demo"})
    assert found.files == {} and found.ignored == 5


def test_a_repository_not_on_the_board_is_not_read(tmp_path):
    reviews = tmp_path / "reviews"
    write(reviews, "elsewhere-12.quality.json", **stamp(repo=f"{OWNER}/elsewhere"))
    found = read(reviews)
    assert found.files == {} and found.ignored == 0


def test_the_tier_is_read_as_low_or_risky_only(tmp_path):
    reviews = tmp_path / "reviews"
    write(reviews, "demo-12.quality.json", **stamp(tier="LOW"))
    assert read(reviews).files[("demo", 12)][0].tier is None


def test_the_time_of_a_claim_is_its_at_or_else_the_files_time(tmp_path):
    reviews = tmp_path / "reviews"
    write(reviews, "demo-12.quality.json", **stamp(verdict="reviewing", at="2026-11-22T10:00:00+01:00"))
    path = write(reviews, "demo-12.voice-architect.json", **stamp(by="voice-architect", verdict="reviewing"))
    os.utime(path, (1_000_000, 1_000_000))
    times = {v.by: v.at for v in read(reviews).files[("demo", 12)]}
    assert times["quality-claude-voice"] == 1795338000.0
    assert times["voice-architect"] == 1_000_000


# c6: a claim changes the reviewer's own file in place.


def test_a_reviewing_claim_keeps_the_files_notes(tmp_path):
    reviews = tmp_path / "reviews"
    path = write(reviews, "demo-12.quality.json", **stamp(verdict="changes", head=OLD, notes=["n1"], followups=["f1"]))
    assert verdicts.claim(path, "quality-claude-voice", HEAD, at="2026-11-22T11:00:00+01:00") is True
    body = json.loads(path.read_text())
    assert (body["verdict"], body["head"], body["at"]) == ("reviewing", HEAD, "2026-11-22T11:00:00+01:00")
    assert body["notes"] == ["n1"] and body["followups"] == ["f1"]


def test_a_reviewer_who_approved_this_head_does_not_claim_it_again(tmp_path):
    reviews = tmp_path / "reviews"
    path = write(reviews, "demo-12.quality.json", **stamp())
    assert verdicts.claim(path, "quality-claude-voice", HEAD, at="2026-11-22T11:00:00+01:00") is False
    assert json.loads(path.read_text())["verdict"] == "approve"


def test_a_claim_on_a_new_file_starts_it(tmp_path):
    reviews = tmp_path / "reviews"
    reviews.mkdir()
    path = reviews / "demo-12.quality.json"
    assert verdicts.claim(path, "quality-claude-voice", HEAD, at="2026-11-22T11:00:00+01:00") is True
    assert json.loads(path.read_text()) == {
        "by": "quality-claude-voice",
        "verdict": "reviewing",
        "head": HEAD,
        "at": "2026-11-22T11:00:00+01:00",
    }


def test_claude_voice_tracker_claim_starts_a_review_in_the_reviewers_own_file(tmp_path, capsys, monkeypatch):
    from claude_voice import cli

    monkeypatch.setattr(cli, "STATE_DIR", Path("~/.local/state/claude-voice"))

    reviews = tmp_path / ".local" / "state" / "claude-voice" / "reviews"
    reviews.mkdir(parents=True)
    write(reviews, "demo-12.quality.json", **stamp(verdict="changes", head=OLD, notes=["n1"]))
    env = {"HOME": str(tmp_path)}
    args = ["tracker", "claim", "demo-12.quality.json", "--by", "quality-claude-voice", "--head", HEAD]
    assert cli.main(args, env=env) == 0
    body = json.loads((reviews / "demo-12.quality.json").read_text())
    assert (body["verdict"], body["head"], body["notes"]) == ("reviewing", HEAD, ["n1"])
    assert "claimed" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("name", "by", "head"),
    [
        ("demo-12.quality.json", "voice-architect", HEAD),  # not that reviewer's file
        ("../demo-12.quality.json", "quality-claude-voice", HEAD),  # not a plain name
        ("demo-12.quality.json", "quality-claude-voice", "abc"),  # not a full head
    ],
)
def test_a_claim_is_refused_for_another_reviewers_file_or_a_short_head(name, by, head, tmp_path, capsys):
    from claude_voice import cli

    (tmp_path / ".local" / "state" / "claude-voice" / "reviews").mkdir(parents=True)
    args = ["tracker", "claim", name, "--by", by, "--head", head]
    assert cli.main(args, env={"HOME": str(tmp_path)}) == 1
    assert "Refused" in capsys.readouterr().err


def test_a_claim_never_writes_through_a_link(tmp_path):
    elsewhere = write(tmp_path, "elsewhere.json", **stamp(verdict="changes", head=OLD))
    reviews = tmp_path / "reviews"
    reviews.mkdir()
    link = reviews / "demo-12.quality.json"
    link.symlink_to(elsewhere)
    with pytest.raises(verdicts.NotPlain):
        verdicts.claim(link, "quality-claude-voice", HEAD, at="2026-11-22T11:00:00+01:00")
    assert json.loads(elsewhere.read_text())["verdict"] == "changes" and link.is_symlink()
    # A link to a file not there yet is refused too, so the claim never creates its target.
    dangling = reviews / "demo-13.quality.json"
    dangling.symlink_to(tmp_path / "made.json")
    with pytest.raises(verdicts.NotPlain):
        verdicts.claim(dangling, "quality-claude-voice", HEAD, at="2026-11-22T11:00:00+01:00")
    assert not (tmp_path / "made.json").exists()


def test_a_claim_on_a_link_is_refused_by_the_command(tmp_path, capsys, monkeypatch):
    from claude_voice import cli

    monkeypatch.setattr(cli, "STATE_DIR", Path("~/.local/state/claude-voice"))

    reviews = tmp_path / ".local" / "state" / "claude-voice" / "reviews"
    reviews.mkdir(parents=True)
    (reviews / "demo-12.quality.json").symlink_to(write(tmp_path, "elsewhere.json", **stamp()))
    args = ["tracker", "claim", "demo-12.quality.json", "--by", "quality-claude-voice", "--head", OLD]
    assert cli.main(args, env={"HOME": str(tmp_path)}) == 1
    assert "Refused" in capsys.readouterr().err


@pytest.mark.parametrize(
    "content",
    ["not json", "[1, 2]", json.dumps(stamp(notes="x" * verdicts.MAX_BYTES)), json.dumps(stamp(by="quality-2"))],
    ids=["not-json", "not-an-object", "too-big", "another-reviewers"],
)
def test_a_claim_never_overwrites_a_file_it_cannot_read_or_another_reviewers(content, tmp_path):
    reviews = tmp_path / "reviews"
    reviews.mkdir()
    path = reviews / "demo-12.quality.json"
    path.write_text(content)
    with pytest.raises(verdicts.NotPlain):
        verdicts.claim(path, "quality-claude-voice", HEAD, at="2026-11-22T11:00:00+01:00")
    assert path.read_text() == content
