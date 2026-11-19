"""The review-prep file: what a reviewer otherwise works out by hand for every pull request.

Each test builds a small repository with a base and a pull request on top, and a fake gh
that answers the one `gh pr view` call. Every name and term here is made up.
"""

import json
import pathlib
import subprocess
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / "scripts"))

import review_prep

CONCURRENCY = review_prep.CONCURRENCY
GUARDED = (
    "def price(x):\n    # RULE: a price is never negative\n    if x < 0:\n        raise ValueError\n    return x\n"
)
ROW = (
    '[[rule]]\nname = "a price is never negative"\nfile = "src/shop.py"\n'
    "find = 'if x < 0:'\nreplace = 'if False:'\n"
)


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout


def write(repo, path, text):
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text)


def commit(repo, message):
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", message)
    return git(repo, "rev-parse", "HEAD").strip()


@pytest.fixture
def repo(tmp_path):
    repo = tmp_path / "shop"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.email", "someone@example.com")
    git(repo, "config", "user.name", "Someone")
    write(repo, "src/shop.py", "def price(x):\n    return x\n")
    write(repo, ".github/workflows/old.yml", "on: push\n")
    base = commit(repo, "chore: Start")
    git(repo, "checkout", "-q", "-b", "feat")
    write(repo, "src/shop.py", GUARDED)
    write(repo, "scripts/mutations/src/shop.py/a-price-is-never-negative.toml", ROW)
    write(repo, "tests/test_shop.py", "def test_a_price_is_never_negative():\n    assert True\n")
    first = commit(repo, "feat: Refuse a negative price\n\nThe Plommeby shop asked.\n\nCo-Authored-By: A <a@x>")
    write(repo, ".github/workflows/checks.yml", "on: pull_request\n" + CONCURRENCY + "jobs: {}\n")
    write(repo, ".github/workflows/other.yml", "on: pull_request\njobs: {}\n")
    name = "r" + "oy"  # the owner word, built from pieces so this file holds no name
    second = commit(repo, f"ci: Add checks\n\n{name}-like words: {name.title()} said so.\n\nClaude-Session: x")
    return {"path": repo, "base": base, "commits": [first, second]}


def fake_gh(repo, body="Red-first: existing behaviour - test_x\nother line"):
    head = repo["commits"][-1]
    answer = {
        "headRefOid": head,
        "baseRefOid": repo["base"],
        "baseRefName": "main",
        "body": body,
        "commits": [{"oid": c, "authors": [{"login": "contributor"}]} for c in repo["commits"]],
        "statusCheckRollup": [
            {"__typename": "CheckRun", "name": "checks", "conclusion": "SUCCESS"},
            {"__typename": "StatusContext", "context": "legacy", "state": "PENDING"},
        ],
    }
    calls = []

    def gh(args):
        calls.append(args)
        return json.dumps(answer)

    gh.calls = calls
    return gh


@pytest.fixture
def terms(tmp_path):
    path = tmp_path / "terms"
    path.write_text("plommeby\n")
    return path


def prep(repo, tmp_path, terms, gh=None, reviews=None):
    out = tmp_path / "prep"
    gh = gh or fake_gh(repo)
    written = review_prep.prepare(
        "someone/shop", 7, repo["path"], terms=terms, out=out, reviews=reviews or tmp_path / "reviews", gh=gh
    )
    return json.loads(written.read_text()), written


def test_the_file_is_named_by_the_head_and_the_patch_sits_beside_it(repo, tmp_path, terms):
    facts, written = prep(repo, tmp_path, terms)
    head = repo["commits"][-1]
    assert written.name == f"shop-7-{head[:12]}.json"
    assert written.with_suffix(".patch").read_text().count("Subject: [PATCH") == 2
    assert (facts["repo"], facts["pr"], facts["head"], facts["base"]) == ("someone/shop", 7, head, repo["base"])
    assert facts["merge_base"] == repo["base"]
    assert facts["at"][19] in "+-"  # a full time, with its offset


def test_a_prepared_head_is_never_written_again(repo, tmp_path, terms):
    _, written = prep(repo, tmp_path, terms)
    written.write_text("kept")
    review_prep.prepare(
        "someone/shop", 7, repo["path"], terms=terms, out=written.parent, reviews=tmp_path, gh=fake_gh(repo)
    )
    assert written.read_text() == "kept"


def test_the_one_gh_call_is_a_literal_pr_view(repo, tmp_path, terms):
    gh = fake_gh(repo)
    prep(repo, tmp_path, terms, gh=gh)
    [args] = gh.calls
    assert args[:5] == ["gh", "pr", "view", "7", "--repo"] and args[5] == "someone/shop"


def test_each_changed_file_has_its_status(repo, tmp_path, terms):
    facts, _ = prep(repo, tmp_path, terms)
    by_path = {f["path"]: f for f in facts["files_changed"]}
    assert by_path["src/shop.py"]["status"] == "M" and by_path["tests/test_shop.py"]["status"] == "A"
    assert by_path["src/shop.py"]["mode_change"] is None


def test_terms_are_counted_never_shown(repo, tmp_path, terms):
    facts, written = prep(repo, tmp_path, terms)
    first, second = facts["commits"]
    assert (first["terms"], second["terms"]) == (1, 0)
    text = written.read_text().lower()
    assert "plommeby" not in text


def test_the_owner_word_is_counted_with_its_context(repo, tmp_path, terms):
    facts, _ = prep(repo, tmp_path, terms)
    second = facts["commits"][1]
    assert second["owner_word"] == 2
    assert all(len(c) <= 63 for c in second["owner_contexts"]) and "said so" in second["owner_contexts"][1]


def test_without_the_terms_file_the_counts_say_so(repo, tmp_path):
    facts, _ = prep(repo, tmp_path, tmp_path / "missing")
    assert facts["commits"][0]["terms"] is None and "missing" in facts["notes"][0]


def test_trailers_other_than_co_authored_by_are_flagged(repo, tmp_path, terms):
    facts, _ = prep(repo, tmp_path, terms)
    assert facts["commits"][0]["trailers"] == ["Co-Authored-By: A <a@x>"]
    assert facts["trailers_ok"] is False


def test_the_patch_id_and_a_restamp_against_the_last_verdict(repo, tmp_path, terms):
    first, _ = prep(repo, tmp_path, terms)
    reviews = tmp_path / "verdicts"
    reviews.mkdir()
    (reviews / "shop-7.quality.json").write_text(json.dumps({"patch_id": first["patch_id_stable"], "head": "a" * 40}))
    facts, _ = prep(repo, tmp_path / "again", terms, reviews=reviews)
    assert facts["prev_patch_id"] == first["patch_id_stable"] and facts["prev_head"] == "a" * 40
    assert facts["restamp_candidate"] is True and len(facts["patch_id_stable"]) == 40


def test_a_row_is_checked_against_the_code_at_head(repo, tmp_path, terms):
    facts, _ = prep(repo, tmp_path, terms)
    [row] = facts["rows"]
    assert row["file"] == "src/shop.py" and row["matches"] == [3]
    assert row["whole_line_replace"] is True and row["compiles"] is True
    assert facts["rules_without_row"] == [] and facts["rows_without_rule"] == []


def test_a_rule_without_a_row_is_named(repo, tmp_path, terms):
    path = repo["path"]
    write(path, "src/shop.py", GUARDED + "    # RULE: a price is rounded\n")
    repo["commits"].append(commit(path, "feat: Round"))
    facts, _ = prep(repo, tmp_path, terms)
    assert facts["rules_without_row"] == [{"file": "src/shop.py", "rule": "a price is rounded"}]


def test_new_tests_red_first_lines_and_checks(repo, tmp_path, terms):
    facts, _ = prep(repo, tmp_path, terms)
    assert facts["new_tests"] == ["test_a_price_is_never_negative"]
    assert facts["red_first_lines"] == ["Red-first: existing behaviour - test_x"]
    assert facts["checks"] == {"checks": "SUCCESS", "legacy": "PENDING"}


def test_each_changed_workflow_says_whether_it_cancels_only_stale_pull_request_runs(repo, tmp_path, terms):
    facts, _ = prep(repo, tmp_path, terms)
    assert facts["concurrency"] == {".github/workflows/checks.yml": True, ".github/workflows/other.yml": False}
