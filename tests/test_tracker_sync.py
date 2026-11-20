"""The board sync keeps a project board in step with the open pull requests and issues.

It writes no text, only two fixed mutations, to one board, about work it read itself.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from claude_voice.tracker import github as sync

OWNER = "example-owner"
PROJECT = "PVT_board"
FIELD = "PVTSSF_status"
OPTIONS = {
    "Backlog": "opt_backlog",
    "In progress": "opt_progress",
    "In review": "opt_review",
    "Waiting on owner": "opt_waiting",
    "Done": "opt_done",
}
REPO = f"{OWNER}/demo"
SETTINGS = sync.Settings(owner=OWNER, number=7, project_id=PROJECT, names=sync.NAMES)


def pr(node: str, *, state="OPEN", draft=False, requested=0, reviews=0, repo=REPO, number=1) -> dict[str, Any]:
    return {
        "__typename": "PullRequest",
        "id": node,
        "number": number,
        "state": state,
        "isDraft": draft,
        "repository": {"nameWithOwner": repo},
        "author": {"login": OWNER},
        "reviewRequests": {"totalCount": requested},
        "reviews": {"totalCount": reviews},
    }


def issue(node: str, *, state="OPEN", author=OWNER, labels=0, commenters=(), repo=REPO) -> dict[str, Any]:
    return {
        "__typename": "Issue",
        "id": node,
        "state": state,
        "repository": {"nameWithOwner": repo},
        "author": {"login": author},
        "labels": {"totalCount": labels},
        "comments": {"nodes": [{"author": {"login": who}} for who in commenters]},
    }


class Fake:
    """GitHub's GraphQL endpoint, answering the two reads and recording every write."""

    def __init__(self, board_items=(), open_work=(), *, project=PROJECT, remaining=4000, repos=(REPO,)):
        self.board = {
            "rateLimit": {"remaining": remaining},
            "user": {
                "projectV2": {
                    "id": project,
                    "field": {"id": FIELD, "options": [{"id": i, "name": n} for n, i in OPTIONS.items()]},
                    "repositories": {"nodes": [{"nameWithOwner": r} for r in repos]},
                    "items": {
                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                        "nodes": [
                            {"id": item, "fieldValueByName": {"optionId": option} if option else None, "content": c}
                            for item, option, c in board_items
                        ],
                    },
                }
            },
        }
        self.open = {"search": {"pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": list(open_work)}}
        self.writes: list[tuple[str, dict[str, Any]]] = []

    def __call__(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        if query == sync.READ_BOARD:
            return self.board
        if query == sync.READ_OPEN:
            return self.open
        self.writes.append((query, variables))
        if query == sync.ADD:
            return {"addProjectV2ItemById": {"item": {"id": f"item_{variables['content']}"}}}
        return {"updateProjectV2ItemFieldValue": {"projectV2Item": {"id": variables["item"]}}}


def _run(fake: Fake, tmp_path, **kw) -> list[str]:
    said: list[str] = []
    assert sync.sync(fake, SETTINGS, tmp_path, say=said.append, **kw) == 0
    return said


def _set(fake: Fake) -> dict[str, str]:
    return {v["item"]: v["option"] for q, v in fake.writes if q == sync.SET}


# --- What each piece of work becomes ---------------------------------------------------


@pytest.mark.parametrize(
    ("work", "status"),
    [
        (pr("P1", draft=True), "In progress"),
        (pr("P1"), "In progress"),
        (pr("P1", requested=1), "In review"),
        (pr("P1", reviews=2), "In review"),
        (issue("I1"), "Backlog"),
        (issue("I1", author="someone-else"), "Waiting on owner"),
    ],
    ids=["draft", "new", "review-asked", "reviewed", "issue", "outside-issue"],
)
def test_open_work_is_added_with_its_status(work, status, tmp_path):
    fake = Fake(open_work=[work])
    _run(fake, tmp_path)
    assert fake.writes[0] == (sync.ADD, {"project": PROJECT, "content": work["id"]})
    assert _set(fake) == {f"item_{work['id']}": OPTIONS[status]}


@pytest.mark.parametrize(
    "work",
    [pr("P1", state="MERGED"), pr("P1", state="CLOSED"), issue("I1", state="CLOSED")],
    ids=["merged", "closed-pr", "closed-issue"],
)
def test_finished_work_on_the_board_is_done(work, tmp_path):
    fake = Fake(board_items=[("item1", OPTIONS["In review"], work)])
    _run(fake, tmp_path)
    assert _set(fake) == {"item1": OPTIONS["Done"]}


def test_a_pull_request_a_reviewer_has_stamped_is_in_review(tmp_path):
    """Reviewers here write a verdict file per pull request, not a GitHub review."""
    reviews = tmp_path / "reviews"
    reviews.mkdir()
    (reviews / "demo-12.quality.json").write_text("{}")
    (reviews / "other-13.quality.json").write_text("{}")
    fake = Fake(open_work=[pr("P12", number=12), pr("P13", number=13)])
    _run(fake, tmp_path)
    assert _set(fake) == {"item_P12": OPTIONS["In review"], "item_P13": OPTIONS["In progress"]}


@pytest.mark.parametrize(
    "name",
    [
        "demo-12.json",  # no reviewer
        "demo-x12.quality.json",  # not a number
        "demo-12.Quality!.json",  # not a plain reviewer slug
        "demo-12.quality.json.bak",  # not a verdict file
        "elsewhere-12.quality.json",  # not one of the board's repositories
        "demo-12xvoice-architectxjson",  # a dot is a dot, not any character
    ],
)
def test_only_a_strictly_named_verdict_file_counts(name, tmp_path):
    reviews = tmp_path / "reviews"
    reviews.mkdir()
    (reviews / name).write_text("{}")
    assert sync.stamped(reviews, {"demo"}) == set()


def test_a_verdict_file_that_is_a_link_does_not_count(tmp_path):
    reviews = tmp_path / "reviews"
    reviews.mkdir()
    real = tmp_path / "real.json"
    real.write_text("{}")
    (reviews / "demo-12.quality.json").symlink_to(real)
    assert sync.stamped(reviews, {"demo"}) == set()
    (reviews / "demo-13.quality.json").write_text("not even json")  # only the name is read
    assert sync.stamped(reviews, {"demo"}) == {("demo", 13)}


def test_finished_work_not_on_the_board_is_not_added(tmp_path):
    fake = Fake(open_work=[pr("P1", state="MERGED")])
    _run(fake, tmp_path)
    assert fake.writes == []


def test_an_outside_issue_leaves_waiting_once_it_is_triaged(tmp_path):
    labelled = issue("I1", author="someone-else", labels=1)
    answered = issue("I2", author="someone-else", commenters=["someone-else", OWNER])
    fake = Fake(
        board_items=[("i1", OPTIONS["Waiting on owner"], labelled), ("i2", OPTIONS["Waiting on owner"], answered)]
    )
    _run(fake, tmp_path)
    assert _set(fake) == {"i1": OPTIONS["Backlog"], "i2": OPTIONS["Backlog"]}


def test_an_open_issue_moved_by_hand_stays_where_it_was_put(tmp_path):
    fake = Fake(board_items=[("i1", OPTIONS["In progress"], issue("I1"))])
    _run(fake, tmp_path)
    assert fake.writes == []


def test_an_unchanged_status_makes_no_write(tmp_path):
    fake = Fake(board_items=[("p1", OPTIONS["In review"], pr("P1", requested=1))], open_work=[pr("P1", requested=1)])
    _run(fake, tmp_path)
    assert fake.writes == []


def test_a_draft_item_on_the_board_is_left_alone(tmp_path):
    fake = Fake(board_items=[("d1", OPTIONS["Backlog"], {"__typename": "DraftIssue"}), ("x", None, None)])
    _run(fake, tmp_path)
    assert fake.writes == []


def test_the_waiting_column_s_name_comes_from_the_settings(tmp_path):
    fake = Fake(open_work=[issue("I1", author="someone-else")])
    fake.board["user"]["projectV2"]["field"]["options"].append({"id": "opt_named", "name": "Waits on the team"})
    settings = sync.Settings(OWNER, 7, PROJECT, {**sync.NAMES, "waiting": "Waits on the team"})
    assert sync.sync(fake, settings, tmp_path, say=lambda _: None) == 0
    assert _set(fake) == {"item_I1": "opt_named"}


# --- The bounds on every write ----------------------------------------------------------


def test_the_board_sync_sends_only_its_two_fixed_writes():
    fake = Fake()
    board = sync.read_board(fake, SETTINGS)
    guard = sync.Guard(board, {"P1"})
    other = sync.ADD.replace("addProjectV2ItemById", "deleteProjectV2Item")
    with pytest.raises(sync.Refused):
        guard.write(fake, other, {"project": PROJECT, "content": "P1"})
    with pytest.raises(sync.Refused):
        guard.write(fake, sync.ADD + "\nmutation { deleteRepository }", {"project": PROJECT, "content": "P1"})
    assert fake.writes == []


def test_the_board_sync_writes_only_to_the_configured_board(tmp_path):
    fake = Fake(open_work=[pr("P1")], project="PVT_someone_elses")
    said = _run(fake, tmp_path)
    assert fake.writes == []
    assert any("not the board the settings name" in line for line in said)
    board = sync.read_board(Fake(), SETTINGS)
    with pytest.raises(sync.Refused):
        sync.Guard(board, {"P1"}).write(fake, sync.ADD, {"project": "PVT_other", "content": "P1"})
    assert fake.writes == []


def test_the_board_sync_writes_only_work_it_read_from_the_board_s_own_repositories(tmp_path):
    fake = Fake(open_work=[pr("P1", repo="someone/else")])
    _run(fake, tmp_path)
    assert fake.writes == []
    board = sync.read_board(fake, SETTINGS)
    with pytest.raises(sync.Refused):
        sync.Guard(board, {"P1"}).write(fake, sync.ADD, {"project": PROJECT, "content": "P_from_elsewhere"})
    assert fake.writes == []


def test_the_board_sync_sets_only_the_status_field_to_one_of_its_options():
    fake = Fake(board_items=[("item1", None, pr("P1"))])
    board = sync.read_board(fake, SETTINGS)
    guard = sync.Guard(board, {"P1"})
    good = {"project": PROJECT, "item": "item1", "field": FIELD, "option": OPTIONS["Done"]}
    for bad in ({**good, "field": "PVTF_title"}, {**good, "option": "opt_made_up"}, {**good, "item": "item_other"}):
        with pytest.raises(sync.Refused):
            guard.write(fake, sync.SET, bad)
    assert fake.writes == []
    guard.write(fake, sync.SET, good)
    assert fake.writes == [(sync.SET, good)]


def test_an_item_from_a_repository_outside_the_board_is_never_touched(tmp_path):
    fake = Fake(board_items=[("item1", OPTIONS["Backlog"], pr("P1", state="MERGED", repo="someone/else"))])
    _run(fake, tmp_path)
    assert fake.writes == []


def test_a_low_rate_limit_skips_the_run_and_says_so_once(tmp_path):
    fake = Fake(open_work=[pr("P1")], remaining=100)
    first = _run(fake, tmp_path)
    second = _run(fake, tmp_path)
    assert fake.writes == []
    assert len([line for line in first if "rate limit" in line]) == 1
    assert not [line for line in second if "rate limit" in line]
    fake.board["rateLimit"]["remaining"] = 4000
    _run(fake, tmp_path)
    assert fake.writes  # back above the floor, it works again


def test_a_dry_run_says_each_write_and_sends_none(tmp_path):
    fake = Fake(open_work=[pr("P1", requested=1)])
    said = _run(fake, tmp_path, dry=True)
    assert fake.writes == []
    assert any("would add" in line and "In review" in line for line in said)


def test_without_a_board_in_the_settings_nothing_is_read(tmp_path):
    config = tmp_path / "config.toml"
    config.write_text('[repos]\nbridge = "a/b"\n')
    assert sync.settings_from(config) is None
    config.write_text(
        f'[tracker]\nowner = "{OWNER}"\nboard = 7\nproject_id = "{PROJECT}"\nwaiting = "Waits on the team"\n'
    )
    assert sync.settings_from(config) == sync.Settings(
        OWNER, 7, PROJECT, {**sync.NAMES, "waiting": "Waits on the team"}
    )


def test_claude_voice_tracker_sync_runs_one_sync_from_the_config(tmp_path, capsys):
    from claude_voice import cli

    config = tmp_path / "config.toml"
    config.write_text(f'[tracker]\nowner = "{OWNER}"\nboard = 7\nproject_id = "{PROJECT}"\n')
    fake = Fake(open_work=[pr("P1")])
    env = {"HOME": str(tmp_path), "CLAUDE_VOICE_CONFIG": str(config)}
    assert cli.tracker_command(True, env, client=fake) == 0
    assert "would add" in capsys.readouterr().out and fake.writes == []
    assert cli.tracker_command(False, {"HOME": str(tmp_path), "CLAUDE_VOICE_CONFIG": "~/none.toml"}, client=fake) == 0
    assert "nothing to sync" in capsys.readouterr().out


def test_the_graphql_client_sends_query_and_variables_as_one_body():
    seen: dict[str, Any] = {}

    def run(cmd, **kw):
        seen["cmd"], seen["body"] = cmd, json.loads(kw["input"])

        class Done:
            returncode, stdout, stderr = 0, json.dumps({"data": {"ok": 1}}), ""

        return Done()

    assert sync.gh_graphql(sync.READ_OPEN, {"q": "x"}, run=run) == {"ok": 1}
    assert seen["cmd"] == ["gh", "api", "graphql", "--input", "-"]
    assert seen["body"] == {"query": sync.READ_OPEN, "variables": {"q": "x"}}
