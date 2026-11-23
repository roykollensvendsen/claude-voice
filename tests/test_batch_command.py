"""The batch command's steps, with real git on local repositories and GitHub stood in for.

Each test builds a small origin with a main branch and pull request refs, as GitHub keeps
them under refs/pull/<n>/head, and stamp files beside it, then runs one step."""

import json
import pathlib
import subprocess
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / "scripts"))

import batch

REPO = "o/claude-voice"
AT = "2026-11-23T20:00:00+01:00"


def git(where, *args, stdin=None):
    command = ["git", "-C", str(where), *args]
    return subprocess.run(command, input=stdin, text=True, capture_output=True, check=True).stdout.strip()


@pytest.fixture(autouse=True)
def identity(monkeypatch):
    for who in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{who}_NAME", "test")
        monkeypatch.setenv(f"GIT_{who}_EMAIL", "test@example.invalid")


class World:
    """An origin with main and pull request refs, a seed clone that makes them, and the stamps."""

    def __init__(self, root):
        self.root = root
        self.origin = root / "origin.git"
        self.seed = root / "seed"
        self.reviews = root / "reviews"
        self.reviews.mkdir()
        subprocess.run(["git", "init", "--quiet", "--bare", "-b", "main", str(self.origin)], check=True)
        subprocess.run(["git", "clone", "--quiet", str(self.origin), str(self.seed)], check=True)
        self.commit("base.txt", "base\n", "base")
        git(self.seed, "push", "--quiet", "origin", "HEAD:refs/heads/main")
        self.base = git(self.seed, "rev-parse", "HEAD")
        self.prs = {}

    def commit(self, name, text, message):
        (self.seed / name).write_text(text, encoding="utf-8")
        git(self.seed, "add", name)
        git(self.seed, "commit", "--quiet", "-m", message)

    def pull_request(self, number, name, text, *, draft=False, stamped=True, tier="low"):
        git(self.seed, "checkout", "--quiet", "--detach", self.base)
        self.commit(name, text, f"pull request {number}")
        head = git(self.seed, "rev-parse", "HEAD")
        git(self.seed, "push", "--quiet", "origin", f"HEAD:refs/pull/{number}/head")
        patch = git(self.seed, "patch-id", "--stable", stdin=git(self.seed, "diff", self.base, head) + "\n").split()[0]
        self.prs[number] = {
            "number": number,
            "headRefOid": head,
            "headRefName": f"feat/{number}",
            "isDraft": draft,
            "statusCheckRollup": [
                {"__typename": "CheckRun", "name": "checks", "status": "COMPLETED", "conclusion": "SUCCESS"}
            ],
            "state": "OPEN",
        }
        if stamped:
            body = {
                "by": "quality-claude-voice",
                "verdict": "approve",
                "head": head,
                "patch_id": patch,
                "tier": tier,
                "at": AT,
                "ratchet_exceptions": [],
                "pr": number,
            }
            (self.reviews / f"claude-voice-{number}.quality.json").write_text(json.dumps(body), encoding="utf-8")
        return head

    def where(self):
        return batch.Where(self.reviews, self.root / "state")


class FakeGh:
    """Answers the few `gh` calls the steps make, from the world and what a test adds."""

    def __init__(self, world):
        self.world = world
        self.answers = {}
        self.calls = []

    def __call__(self, args):
        self.calls.append(list(args))
        key = " ".join(args[:3])
        if key in self.answers:
            return json.dumps(self.answers[key])
        if args[:2] == ["pr", "list"]:
            return json.dumps(list(self.world.prs.values()))
        if args[:2] == ["pr", "view"]:
            return json.dumps(self.world.prs[int(args[2])])
        raise AssertionError(f"unexpected gh call: {args}")


@pytest.fixture
def world(tmp_path):
    return World(tmp_path)


def build(world, gh, owner_yes=(), work="work"):
    where = world.where()
    return batch.build(
        gh, REPO, str(world.origin), world.root / work, world.root / "body.md", where, owner_yes, "2026-11-24"
    )


def test_build_applies_the_cleared_pull_requests_in_order_and_leaves_out_the_rest(world):
    world.pull_request(4, "four.txt", "4\n")
    world.pull_request(9, "nine.txt", "9\n")
    world.pull_request(12, "twelve.txt", "12\n", draft=True)
    world.pull_request(13, "thirteen.txt", "13\n")
    moved = json.loads((world.reviews / "claude-voice-13.quality.json").read_text(encoding="utf-8"))
    moved["patch_id"] = "0" * 40
    (world.reviews / "claude-voice-13.quality.json").write_text(json.dumps(moved), encoding="utf-8")
    world.pull_request(14, "fourteen.txt", "14\n", stamped=False)
    commands = build(world, FakeGh(world))
    record = batch.parse_body((world.root / "body.md").read_text(encoding="utf-8"))
    assert record.branch == "integration/2026-11-24"
    assert record.base == world.base
    assert [e.number for e in record.members] == [4, 9]
    work = world.root / "work"
    assert git(work, "rev-parse", record.branch) == record.members[-1].tip
    assert git(work, "show", f"{record.branch}:nine.txt") == "9"
    body = (world.root / "body.md").read_text(encoding="utf-8")
    assert "#12: it is a draft" in body
    assert "#13: quality-claude-voice stamped patch 00000000, and as applied it is" in body
    assert "#14" not in body
    assert commands == [
        f"git -C {work} push origin integration/2026-11-24",
        f"gh pr create --repo {REPO} --draft --base main --head integration/2026-11-24"
        f" --title 'batch: integration/2026-11-24' --body-file {world.root / 'body.md'}",
    ]


def test_build_leaves_out_a_pull_request_that_does_not_apply_on_the_batch(world):
    world.pull_request(4, "shared.txt", "four\n")
    world.pull_request(9, "shared.txt", "nine\n")
    build(world, FakeGh(world))
    body = (world.root / "body.md").read_text(encoding="utf-8")
    assert "#9: it does not apply cleanly on the batch so far" in body
    assert [e.number for e in batch.parse_body(body).members] == [4]


def test_build_takes_the_next_free_branch_name(world):
    world.pull_request(4, "four.txt", "4\n")
    git(world.seed, "push", "--quiet", "origin", "HEAD:refs/heads/integration/2026-11-24")
    build(world, FakeGh(world))
    body = (world.root / "body.md").read_text(encoding="utf-8")
    assert batch.parse_body(body).branch == "integration/2026-11-24-2"


def test_build_stops_when_nothing_is_cleared(world):
    world.pull_request(12, "twelve.txt", "12\n", draft=True)
    with pytest.raises(batch.Stop, match="nothing is cleared: #12: it is a draft"):
        build(world, FakeGh(world))


def risky(world):
    head = world.pull_request(4, "four.txt", "4\n", tier="risky")
    other = {"by": "voice-architect", "verdict": "approve", "head": head, "tier": "risky", "at": AT, "pr": 4}
    stamp = json.loads((world.reviews / "claude-voice-4.quality.json").read_text(encoding="utf-8"))
    other["patch_id"], other["ratchet_exceptions"] = stamp["patch_id"], []
    (world.reviews / "claude-voice-4.voice-architect.json").write_text(json.dumps(other), encoding="utf-8")
    return head


def test_build_takes_the_owners_yes_from_an_accepted_check_in_the_log(world):
    head = risky(world)
    (world.root / "state").mkdir()
    accepted = {
        "at": AT,
        "decision": {"item": "claude-voice#4", "sha": head, "action": "merge", "uuid": "u-4"},
        "uuid": "u-4",
        "sha_matched": True,
        "result": "accepted",
    }
    lines = ["{not json", "[1, 2]", json.dumps(accepted)]
    (world.root / "state" / "decision-checks.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    build(world, FakeGh(world))
    assert [e.number for e in batch.parse_body((world.root / "body.md").read_text(encoding="utf-8")).members] == [4]


def test_build_takes_the_owners_yes_from_the_manager_for_a_risky_pull_request(world):
    head = risky(world)
    with pytest.raises(batch.Stop, match="the owner's yes for its head is not recorded"):
        build(world, FakeGh(world))
    build(world, FakeGh(world), [f"4:{head}"], work="work-2")
    assert [e.tier for e in batch.parse_body((world.root / "body.md").read_text(encoding="utf-8")).members] == ["risky"]


def built(world):
    world.pull_request(4, "four.txt", "4\n")
    world.pull_request(9, "nine.txt", "9\n")
    build(world, FakeGh(world))
    body = (world.root / "body.md").read_text(encoding="utf-8")
    return body, batch.parse_body(body)


def batch_view(body, head, conclusion="SUCCESS"):
    quality = {
        "__typename": "CheckRun",
        "name": "quality",
        "status": "COMPLETED",
        "conclusion": conclusion,
        "detailsUrl": "https://run/7",
    }
    return {"body": body, "headRefOid": head, "statusCheckRollup": [quality]}


def test_merge_list_prints_the_status_and_merge_lines_for_a_green_batch(world):
    body, record = built(world)
    gh = FakeGh(world)
    gh.answers["pr view 500"] = batch_view(body, record.members[-1].tip)
    gh.answers[f"api repos/{REPO}/commits/main"] = {"sha": record.base}
    lines = batch.merge_commands(gh, REPO, 500, world.where(), [])
    heads = [world.prs[4]["headRefOid"], world.prs[9]["headRefOid"]]
    assert lines[:2] == batch.status_lines(REPO, heads, record.branch, "https://run/7")
    assert lines[2] == f"gh pr merge 4 --repo {REPO} --rebase --match-head-commit {heads[0]}"
    assert sum(line.startswith("gh pr merge") for line in lines) == 2


def test_merge_list_refuses_a_batch_whose_quality_run_is_not_green(world):
    body, record = built(world)
    gh = FakeGh(world)
    gh.answers["pr view 500"] = batch_view(body, record.members[-1].tip, "FAILURE")
    gh.answers[f"api repos/{REPO}/commits/main"] = {"sha": record.base}
    with pytest.raises(batch.Stop, match="quality run is not green"):
        batch.merge_commands(gh, REPO, 500, world.where(), [])


def test_merge_list_refuses_when_a_member_has_a_new_commit(world):
    body, record = built(world)
    world.prs[9]["headRefOid"] = "f" * 40
    gh = FakeGh(world)
    gh.answers["pr view 500"] = batch_view(body, record.members[-1].tip)
    gh.answers[f"api repos/{REPO}/commits/main"] = {"sha": record.base}
    with pytest.raises(batch.Stop, match="#9 is no longer cleared: it has a new commit since the batch was built"):
        batch.merge_commands(gh, REPO, 500, world.where(), [])


def test_a_pull_request_without_a_batch_record_stops_every_step(world):
    gh = FakeGh(world)
    gh.answers["pr view 500"] = {"body": "by hand", "headRefOid": "a" * 40}
    with pytest.raises(batch.Stop, match="#500 holds no batch record"):
        batch.merge_commands(gh, REPO, 500, world.where(), [])


def merged(world, record):
    work = world.root / "work"
    git(work, "push", "--quiet", "origin", f"{record.branch}:refs/heads/main")
    git(work, "push", "--quiet", "origin", record.branch)
    for n in (4, 9):
        world.prs[n]["state"] = "MERGED"
    return work


def runs(*found):
    return [
        {"headSha": sha, "status": status, "conclusion": conclusion, "url": f"https://run/{sha[:4]}"}
        for sha, status, conclusion in found
    ]


def test_verify_reports_main_at_the_batch_tree_and_its_run(world):
    body, record = built(world)
    work = merged(world, record)
    gh = FakeGh(world)
    gh.answers["pr view 500"] = batch_view(body, record.members[-1].tip)
    tip = record.members[-1].tip
    gh.answers["run list --repo"] = runs((tip, "completed", "success"))
    lines = batch.verify(gh, REPO, 500, work)
    tree = git(work, "rev-parse", f"{tip}^{{tree}}")
    assert lines == [f"main holds the batch's tree {tree}", f"main's run: success, https://run/{tip[:4]}"]


def test_verify_waits_while_main_has_no_complete_run_there(world):
    body, record = built(world)
    work = merged(world, record)
    gh = FakeGh(world)
    gh.answers["pr view 500"] = batch_view(body, record.members[-1].tip)
    gh.answers["run list --repo"] = runs((record.members[-1].tip, "completed", "cancelled"))
    with pytest.raises(batch.Stop, match="waiting for a complete main run"):
        batch.verify(gh, REPO, 500, work)


def test_verify_stops_when_main_never_reached_the_batch_tree(world):
    body, record = built(world)
    work = merged(world, record)
    git(world.seed, "fetch", "--quiet", "origin", "main")
    git(world.seed, "checkout", "--quiet", "--detach", world.base)
    world.commit("other.txt", "other\n", "something else")
    git(world.seed, "push", "--quiet", "--force", "origin", "HEAD:refs/heads/main")
    gh = FakeGh(world)
    gh.answers["pr view 500"] = batch_view(body, record.members[-1].tip)
    with pytest.raises(batch.Stop, match="stop, and open an issue"):
        batch.verify(gh, REPO, 500, work)


def test_verify_names_the_prefix_when_a_merge_failed_part_way(world):
    body, record = built(world)
    world.prs[4]["state"] = "MERGED"
    gh = FakeGh(world)
    gh.answers["pr view 500"] = batch_view(body, record.members[-1].tip)
    with pytest.raises(batch.Stop, match="merged: #4\nfailed: #9"):
        batch.verify(gh, REPO, 500, world.root / "work")


def test_bisect_runs_each_prefix_in_the_sandbox_and_names_the_culprit(world):
    body, record = built(world)
    gh = FakeGh(world)
    gh.answers["pr view 500"] = batch_view(body, record.members[-1].tip)
    ran = []

    def run(command, cwd):
        ran.append(command[0])
        return 1 if command[0] == str(batch.SANDBOX) and (cwd / "nine.txt").exists() else 0

    found = batch.bisect(gh, REPO, 500, world.root / "work", world.root / "copy", ["pytest"], run)
    assert found == "culprit: #9"
    assert set(ran) == {"uv", str(batch.SANDBOX)}


def test_close_says_where_each_member_landed_and_prints_the_commands(world):
    body, record = built(world)
    world.prs[4].update(state="MERGED", mergeCommit={"oid": "1" * 40})
    gh = FakeGh(world)
    gh.answers["pr view 500"] = batch_view(body, record.members[-1].tip)
    note = world.root / "close.md"
    lines = batch.close(gh, REPO, 500, note)
    assert note.read_text(encoding="utf-8").splitlines() == [
        f"Closed: this batch's pull requests now stand on main as follows ({record.branch}).",
        "",
        f"- #4 landed as {'1' * 40}",
        "- #9 is OPEN, not merged",
    ]
    assert lines == [f"gh pr comment 500 --repo {REPO} --body-file {note}", f"gh pr close 500 --repo {REPO}"]


def test_doctor_reads_main_protection_and_the_command_exits_on_drift(world, capsys):
    gh = FakeGh(world)
    gh.answers[f"api repos/{REPO}/branches/main/protection/required_status_checks"] = {"contexts": ["quality"]}
    assert batch.main(["--repo", REPO, "doctor"], gh) == 1
    assert "does not require batch" in capsys.readouterr().out
    gh.answers[f"api repos/{REPO}/branches/main/protection/required_status_checks"] = {"contexts": ["batch"]}
    assert batch.main(["--repo", REPO, "doctor"], gh) == 0


def test_the_command_prints_a_stop_and_exits_with_one(world, capsys):
    gh = FakeGh(world)
    gh.answers["pr view 500"] = {"body": "by hand", "headRefOid": "a" * 40}
    args = ["--repo", REPO, "--reviews", str(world.reviews), "--state", str(world.root), "merge-list", "--pr", "500"]
    assert batch.main(args, gh) == 1
    assert "holds no batch record" in capsys.readouterr().err


def test_the_command_builds_from_the_named_origin(world, capsys):
    world.pull_request(4, "four.txt", "4\n")
    args = ["--repo", REPO, "--reviews", str(world.reviews), "--state", str(world.root), "build"]
    args += ["--work", str(world.root / "work"), "--body", str(world.root / "body.md"), "--origin", str(world.origin)]
    assert batch.main(args, FakeGh(world)) == 0
    assert "gh pr create" in capsys.readouterr().out
