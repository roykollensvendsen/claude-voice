"""`claude-voice tracker status --check`: open pull requests mapped to the three states,
each line through the privacy gate, and nothing ever written (docs/tracker.md).

Every pull request here is made up, and nothing reaches GitHub: the adapter is given a
fake command line that answers from these fixtures and records what it was asked.
"""

import json
from datetime import UTC, datetime, timedelta

import pytest

from claude_voice import cli, software
from claude_voice.tracker import base, github, status

OWNER = "example-owner"
REPO = f"{OWNER}/example-repo"
NOW = datetime(2026, 1, 10, 12, 0, tzinfo=UTC)
#: A made-up word standing for a home place on the owner's private list.
HOME_WORD = "Mapleford"


def pr(number=1, **fields):
    """An open pull request as `gh pr list --json` gives it, with nothing wrong unless told."""
    found = {
        "number": number,
        "title": f"feat: Example change {number}",
        "url": f"https://github.com/{REPO}/pull/{number}",
        "isDraft": False,
        "mergeable": "MERGEABLE",
        "baseRefName": "main",
        "updatedAt": (NOW - timedelta(hours=1)).isoformat(),
        "reviewRequests": [],
        "statusCheckRollup": [check("tests", "SUCCESS")],
    }
    return {**found, **fields}


def check(name, conclusion, started="2026-01-10T11:00:00Z"):
    return {
        "__typename": "CheckRun",
        "name": name,
        "status": "COMPLETED",
        "conclusion": conclusion,
        "startedAt": started,
    }


def word_list(folder, content):
    """A private word list the way the owner keeps it: his alone."""
    path = folder / "private.toml"
    path.write_text(content)
    path.chmod(0o600)
    return path


def state(**fields):
    return github.state_of(pr(**fields), OWNER, NOW)


def fake_gh(prs, visibility="PUBLIC", calls=None):
    """GitHub's command line as the adapter reads it, answering from fixtures."""

    def run(args):
        if calls is not None:
            calls.append(list(args))
        if args[:2] == ["repo", "view"]:
            return json.dumps({"nameWithOwner": REPO, "visibility": visibility})
        if args[:2] == ["pr", "list"]:
            return json.dumps(prs)
        return "{}"

    return run


# -- the mapping, from fixed GitHub facts --------------------------------------------------


def test_an_open_pull_request_with_nothing_wrong_is_moving():
    assert state() == ("moving", "no check has failed and no conflict")
    assert state(isDraft=True)[0] == "moving"


def test_a_review_asked_of_the_owner_waits_on_the_owner():
    asked = [{"__typename": "User", "login": "Example-Owner"}]
    assert state(reviewRequests=asked) == ("waits_on_owner", "a review is asked of the owner")
    assert state(reviewRequests=[{"__typename": "User", "login": "someone-else"}])[0] == "moving"


def test_a_failed_check_stops_a_pull_request():
    found = state(statusCheckRollup=[check("tests", "SUCCESS"), check("lint", "FAILURE")])
    assert found == ("stopped", "a check failed: lint")


def test_only_the_latest_run_of_a_check_counts():
    rerun = [check("tests", "FAILURE", "2026-01-10T10:00:00Z"), check("tests", "SUCCESS", "2026-01-10T11:00:00Z")]
    assert state(statusCheckRollup=rerun)[0] == "moving"
    assert state(statusCheckRollup=[check("tests", "CANCELLED")])[0] == "moving"


def test_a_conflict_with_its_base_stops_a_pull_request():
    assert state(mergeable="CONFLICTING") == ("stopped", "it conflicts with main")


def test_a_pull_request_quiet_for_a_day_is_stopped():
    assert state(updatedAt=(NOW - timedelta(hours=30)).isoformat()) == ("stopped", "no activity for 30 h")
    assert state(updatedAt=(NOW - timedelta(hours=23)).isoformat())[0] == "moving"


def test_waiting_on_the_owner_comes_before_a_stop():
    found = state(reviewRequests=[{"login": OWNER}], mergeable="CONFLICTING", statusCheckRollup=[check("t", "FAILURE")])
    assert found[0] == "waits_on_owner"


def test_the_github_adapter_reads_open_pull_requests_as_items():
    adapter = github.GitHub(REPO, run=fake_gh([pr(7, mergeable="CONFLICTING"), pr(8)]), now=lambda: NOW)
    items = adapter.items()
    assert [(i.id, i.state, i.component) for i in items] == [("#7", "stopped", REPO), ("#8", "moving", REPO)]


# -- nothing is ever written -----------------------------------------------------------------


def test_the_github_adapter_runs_only_reading_commands():
    calls = []
    adapter = github.GitHub(REPO, run=fake_gh([], calls=calls))
    for write in (["pr", "merge", "1"], ["issue", "create"], ["api", "graphql"], ["project", "item-edit"]):
        with pytest.raises(github.GitHubError, match="only reads"):
            adapter._read(*write)
    assert calls == []


def test_the_write_methods_refuse():
    adapter = github.GitHub(REPO, run=fake_gh([]))
    item = base.Item("#1", "t", REPO, "moving", "why", "", None)
    for write in (lambda: adapter.open_item(REPO, "t", "b"), lambda: adapter.set_state(item, "stopped")):
        with pytest.raises(base.ReadOnly):
            write()
    with pytest.raises(base.ReadOnly):
        adapter.link_change(item, "#2")


def test_the_status_check_calls_no_write_method(monkeypatch, tmp_path, capsys):
    written, calls = [], []
    for name in ("open_item", "set_state", "link_change"):
        monkeypatch.setattr(base.Tracker, name, lambda *a, _n=name, **k: written.append(_n))
    monkeypatch.setattr(software, "_gh", fake_gh([pr(3, updatedAt=datetime.now(UTC).isoformat())], calls=calls))
    code = cli.main(["tracker", "status", "--check", "--repo", REPO], env={"HOME": str(tmp_path)})
    assert code == 0
    assert written == []
    assert calls and all(tuple(c[:2]) in github.READS for c in calls)
    assert capsys.readouterr().out.splitlines() == [
        "#3 moving: no check has failed and no conflict",
        f"{REPO}: 1 open pull request: 1 moving, 0 stopped, 0 waiting on the owner",
    ]


def test_the_status_check_without_check_reads_nothing(monkeypatch, tmp_path, capsys):
    calls = []
    monkeypatch.setattr(software, "_gh", fake_gh([], calls=calls))
    assert cli.main(["tracker", "status"], env={"HOME": str(tmp_path)}) == 2
    assert calls == []
    assert "only `tracker status --check` is built" in capsys.readouterr().err


# -- the privacy gate --------------------------------------------------------------------------


def test_a_status_line_with_private_data_is_refused():
    items = [
        base.Item("#4", "t", REPO, "stopped", f"a check failed: deploy to {HOME_WORD}", "", None),
        base.Item("#5", "t", REPO, "moving", "no check has failed and no conflict", "", None),
    ]
    printed, refused = status.lines(REPO, items, frozenset({HOME_WORD.lower()}))
    assert refused == 1
    assert printed[0] == "#4: refused by the privacy gate, in its why"
    assert printed[1] == "#5 moving: no check has failed and no conflict"
    assert not any(HOME_WORD.lower() in line.lower() for line in printed)


def test_a_refused_summary_is_counted():
    printed, refused = status.lines(f"{OWNER}/{HOME_WORD}-repo", [], frozenset({HOME_WORD}))
    assert printed == ["the summary was refused by the privacy gate"] and refused == 1


def test_a_check_name_with_a_home_word_fails_the_status_check(monkeypatch, tmp_path, capsys):
    words = word_list(tmp_path, f'words = ["{HOME_WORD}"]\n')
    failing = pr(6, statusCheckRollup=[check(f"deploy to {HOME_WORD}", "FAILURE")])
    monkeypatch.setattr(software, "_gh", fake_gh([failing]))
    code = cli.main(
        ["tracker", "status", "--check", "--repo", REPO, "--words", str(words)], env={"HOME": str(tmp_path)}
    )
    out = capsys.readouterr().out
    assert code == 1
    assert HOME_WORD not in out
    assert "#6: refused by the privacy gate, in its why" in out


def test_lines_are_told_in_the_order_of_the_states():
    items = [base.Item(f"#{n}", "t", REPO, s, "why", "", None) for n, s in ((1, "moving"), (2, "waits_on_owner"))]
    assert [line.split()[0] for line in status.lines(REPO, items, frozenset())[0][:2]] == ["#2", "#1"]


def test_a_broken_private_word_list_refuses_the_check(tmp_path):
    for content in ("words = [", 'words = "one"', "other = []"):
        broken = word_list(tmp_path, content)
        with pytest.raises(status.WordsError, match="cannot be read"):
            status.private_words(broken)
    with pytest.raises(status.WordsError):
        status.private_words(tmp_path)  # a folder
    dangling = tmp_path / "gone.toml"
    dangling.symlink_to(tmp_path / "nowhere.toml")
    with pytest.raises(status.WordsError):
        status.private_words(dangling)


def test_a_missing_private_word_list_is_said_plainly(tmp_path):
    words, note = status.private_words(tmp_path / "none.toml")
    assert words == frozenset() and "no private word list" in (note or "")
    listed = word_list(tmp_path, 'words = ["Exampleton"]')
    assert status.private_words(listed) == (frozenset({"Exampleton"}), None)


def test_a_broken_word_list_stops_the_command_before_github_is_read(monkeypatch, tmp_path, capsys):
    calls = []
    monkeypatch.setattr(software, "_gh", fake_gh([], calls=calls))
    word_list(tmp_path, "words = [")
    argv = ["tracker", "status", "--check", "--words", str(tmp_path / "private.toml")]
    assert cli.main(argv, env={"HOME": str(tmp_path)}) == 2
    assert calls == []
    assert "cannot be read" in capsys.readouterr().err


def test_a_repository_that_is_not_public_is_refused(monkeypatch, tmp_path, capsys):
    calls = []
    monkeypatch.setattr(software, "_gh", fake_gh([pr(1)], visibility="PRIVATE", calls=calls))
    assert cli.main(["tracker", "status", "--check", "--repo", REPO], env={"HOME": str(tmp_path)}) == 1
    assert "is not public" in capsys.readouterr().err
    assert [c[:2] for c in calls] == [["repo", "view"]]


def test_github_not_answering_says_the_next_step(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(software, "_gh", lambda args: None)
    assert cli.main(["tracker", "status", "--check", "--repo", REPO], env={"HOME": str(tmp_path)}) == 1
    assert "gh auth status" in capsys.readouterr().err


def test_a_cancelled_run_never_hides_an_older_failure():
    runs = [check("tests", "FAILURE", "2026-01-10T10:00:00Z"), check("tests", "CANCELLED", "2026-01-10T11:00:00Z")]
    assert state(statusCheckRollup=runs) == ("stopped", "a check failed: tests")


def test_a_repository_that_is_not_owner_and_name_is_refused(monkeypatch, tmp_path, capsys):
    calls = []
    monkeypatch.setattr(software, "_gh", fake_gh([], calls=calls))
    for bad in ("example-repo", "--web", "a/b/c", "owner/name; echo", ""):
        with pytest.raises(github.GitHubError, match="owner/name"):
            github.GitHub(bad)
    assert cli.main(["tracker", "status", "--check", "--repo=-x/y"], env={"HOME": str(tmp_path)}) == 1
    assert calls == []
    assert "owner/name" in capsys.readouterr().err
    assert github.GitHub(REPO).repo == REPO


def test_a_private_word_list_others_may_read_refuses_the_check(tmp_path):
    listed = word_list(tmp_path, 'words = ["Exampleton"]')
    for mode in (0o644, 0o640, 0o602):
        listed.chmod(mode)
        with pytest.raises(status.WordsError, match="chmod 600"):
            status.private_words(listed)


def test_an_empty_private_word_refuses_the_check(tmp_path):
    for content in ('words = [""]', 'words = ["Exampleton", "  "]'):
        with pytest.raises(status.WordsError, match="cannot be read"):
            status.private_words(word_list(tmp_path, content))


def test_the_summary_says_when_the_read_limit_was_reached():
    many = [pr(n) for n in range(1, github.LIMIT + 1)]
    adapter = github.GitHub(REPO, run=fake_gh(many), now=lambda: NOW)
    items = adapter.items()
    assert adapter.capped == github.LIMIT
    said = status.lines(REPO, items, frozenset(), adapter.capped)[0][-1]
    assert said == f"only the first {github.LIMIT} open pull requests were read; more may be open"
    fewer = github.GitHub(REPO, run=fake_gh(many[:3]), now=lambda: NOW)
    fewer.items()
    assert fewer.capped is None
