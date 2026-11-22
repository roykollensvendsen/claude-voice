"""The board sync keeps a project board in step with the open pull requests and issues.

It writes no text, only three fixed mutations, to one board, about work it read itself.
Which column each piece of work belongs in is tested in test_tracker_flow.
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from typing import Any

import pytest

from claude_voice.tracker import github as sync

OWNER = "example-owner"
PROJECT = "PVT_board"
FIELD = "PVTSSF_status"
HEALTH = "PVTSSF_health"
STALLED = "opt_stalled"
OPTIONS = {name: f"opt_{kind}" for kind, name in sync.NAMES.items()}
REPO = f"{OWNER}/demo"
SETTINGS = sync.Settings(owner=OWNER, number=7, project_id=PROJECT, names=sync.NAMES)
HEAD = "a" * 40


def iso(seconds_ago=0.0):
    return datetime.fromtimestamp(time.time() - seconds_ago, UTC).isoformat().replace("+00:00", "Z")


def pr(node: str, *, state="OPEN", draft=False, repo=REPO, number=1, checks="SUCCESS", quiet=0.0) -> dict[str, Any]:
    return {
        "__typename": "PullRequest",
        "id": node,
        "number": number,
        "state": state,
        "isDraft": draft,
        "headRefOid": HEAD,
        "updatedAt": iso(quiet),
        "repository": {"nameWithOwner": repo},
        "author": {"login": OWNER},
        "commits": {"nodes": [{"commit": {"oid": HEAD, "statusCheckRollup": {"state": checks}}}]},
    }


def issue(
    node: str, *, state="OPEN", author=OWNER, labels=0, commenters=(), repo=REPO, assignees=0, quiet=0.0
) -> dict[str, Any]:
    return {
        "__typename": "Issue",
        "id": node,
        "state": state,
        "updatedAt": iso(quiet),
        "repository": {"nameWithOwner": repo},
        "author": {"login": author},
        "labels": {"totalCount": labels},
        "comments": {"nodes": [{"author": {"login": who}} for who in commenters]},
        "assignees": {"totalCount": assignees},
        "closedByPullRequestsReferences": {"totalCount": 0},
    }


class Fake:
    """GitHub's GraphQL endpoint, answering the two reads and recording every write.

    A board item is (item id, Status option id, content), or with a Health option id and a
    "Blocked by" text after the content.
    """

    def __init__(self, board_items=(), open_work=(), *, project=PROJECT, remaining=4000, repos=(REPO,), health=True):
        nodes = []
        for item, option, content, *rest in board_items:
            stalled, reason = (rest + [None, None])[:2]
            nodes.append(
                {
                    "id": item,
                    "status": {"optionId": option} if option else None,
                    "health": {"optionId": stalled} if stalled else None,
                    "reason": {"text": reason} if reason is not None else None,
                    "content": content,
                }
            )
        self.board = {
            "rateLimit": {"remaining": remaining},
            "user": {
                "projectV2": {
                    "id": project,
                    "status": {"id": FIELD, "options": [{"id": i, "name": n} for n, i in OPTIONS.items()]},
                    "health": {"id": HEALTH, "options": [{"id": STALLED, "name": "Stalled"}]} if health else None,
                    "repositories": {"nodes": [{"nameWithOwner": r} for r in repos]},
                    "items": {"pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": nodes},
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
    return {v["item"]: v["option"] for q, v in fake.writes if q == sync.SET and v["field"] == FIELD}


def _health(fake: Fake) -> dict[str, str | None]:
    return {v["item"]: v.get("option") for q, v in fake.writes if q in (sync.SET, sync.CLEAR) and v["field"] == HEALTH}


def verdict(tmp_path, name, **body):
    reviews = tmp_path / "reviews"
    reviews.mkdir(exist_ok=True)
    (reviews / name).write_text(json.dumps({"head": HEAD, "repo": REPO} | body))


# --- What each piece of work becomes, end to end ----------------------------------------


@pytest.mark.parametrize(
    ("work", "status"),
    [
        (pr("P1", draft=True), "In progress"),
        (pr("P1"), "Waiting for review"),
        (issue("I1"), "Backlog"),
        (issue("I1", author="someone-else"), "Waiting on owner"),
        (issue("I1", assignees=1), "In progress"),
    ],
    ids=["draft", "ready-for-review", "issue", "outside-issue", "taken-issue"],
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
    fake = Fake(board_items=[("item1", OPTIONS["Being reviewed"], work)])
    _run(fake, tmp_path)
    assert _set(fake) == {"item1": OPTIONS["Done"]}


def test_the_verdict_files_move_a_pull_request_along(tmp_path):
    """A low pull request with one approval at its head and green checks is ready to merge."""
    verdict(tmp_path, "demo-12.quality.json", by="quality-claude-voice", verdict="reviewing", pr=12)
    verdict(tmp_path, "demo-13.quality.json", by="quality-claude-voice", verdict="approve", tier="low", pr=13)
    fake = Fake(open_work=[pr("P12", number=12), pr("P13", number=13)])
    _run(fake, tmp_path)
    assert _set(fake) == {"item_P12": OPTIONS["Being reviewed"], "item_P13": OPTIONS["Ready to merge"]}


def test_ignored_verdict_files_are_counted_in_the_runs_summary(tmp_path):
    verdict(tmp_path, "demo-12.anything.json", by="anything", verdict="approve")
    said = _run(Fake(), tmp_path)
    assert any("ignored 1 verdict file" in line for line in said)


def test_finished_work_not_on_the_board_is_not_added(tmp_path):
    fake = Fake(open_work=[pr("P1", state="MERGED")])
    _run(fake, tmp_path)
    assert fake.writes == []


def test_hand_moves_survive_a_run(tmp_path):
    """Blocked and To do are moved by hand, and the sync leaves them there."""
    fake = Fake(
        board_items=[
            ("b", OPTIONS["Blocked"], pr("P1"), None, "waits on #439 merge"),
            ("t", OPTIONS["To do"], issue("I1")),
            ("k", OPTIONS["Backlog"], issue("I2")),
        ]
    )
    _run(fake, tmp_path)
    assert fake.writes == []


def test_a_hand_move_into_a_column_the_sync_owns_is_put_right(tmp_path):
    fake = Fake(board_items=[("p", OPTIONS["Ready to merge"], pr("P1"))])
    _run(fake, tmp_path)
    assert _set(fake) == {"p": OPTIONS["Waiting for review"]}


def test_an_unassigned_issue_that_was_in_progress_goes_back_to_to_do(tmp_path):
    fake = Fake(board_items=[("i1", OPTIONS["In progress"], issue("I1"))])
    _run(fake, tmp_path)
    assert _set(fake) == {"i1": OPTIONS["To do"]}


def test_an_unchanged_status_makes_no_write(tmp_path):
    """c7: GitHub keeps when a Status last changed, so the same value is never written again."""
    fake = Fake(board_items=[("p1", OPTIONS["Waiting for review"], pr("P1"))], open_work=[pr("P1")])
    _run(fake, tmp_path)
    assert fake.writes == []


def test_a_draft_item_on_the_board_is_left_alone(tmp_path):
    fake = Fake(board_items=[("d1", OPTIONS["Backlog"], {"__typename": "DraftIssue"}), ("x", None, None)])
    _run(fake, tmp_path)
    assert fake.writes == []


def test_the_waiting_column_s_name_comes_from_the_settings(tmp_path):
    fake = Fake(open_work=[issue("I1", author="someone-else")])
    fake.board["user"]["projectV2"]["status"]["options"].append({"id": "opt_named", "name": "Waits on the team"})
    settings = sync.Settings(OWNER, 7, PROJECT, {**sync.NAMES, "waiting": "Waits on the team"})
    assert sync.sync(fake, settings, tmp_path, say=lambda _: None) == 0
    assert _set(fake) == {"item_I1": "opt_named"}


def test_a_column_the_board_does_not_have_is_left_alone(tmp_path):
    """Until the owner's board step, a board without the new columns is not touched for them."""
    fake = Fake(board_items=[("p", OPTIONS["Backlog"], pr("P1"))])
    del fake.board["user"]["projectV2"]["status"]["options"][3]  # Waiting for review
    _run(fake, tmp_path)
    assert fake.writes == []


# --- Health --------------------------------------------------------------------------------


def test_quiet_work_is_flagged_stalled_and_cleared_once_it_moves(tmp_path):
    quiet = Fake(board_items=[("p", OPTIONS["In progress"], pr("P1", draft=True, quiet=3600))])
    _run(quiet, tmp_path)
    assert _health(quiet) == {"p": STALLED}
    moving = Fake(board_items=[("p", OPTIONS["In progress"], pr("P1", draft=True), STALLED)])
    _run(moving, tmp_path)
    assert moving.writes == [(sync.CLEAR, {"project": PROJECT, "item": "p", "field": HEALTH})]


def test_blocked_with_no_reason_is_flagged_stalled(tmp_path):
    fake = Fake(board_items=[("b", OPTIONS["Blocked"], issue("I1"), None, "")])
    _run(fake, tmp_path)
    assert _health(fake) == {"b": STALLED}


def test_an_item_already_flagged_stalled_gets_no_write(tmp_path):
    """c7: setting Stalled on an item that is already Stalled sends nothing."""
    fake = Fake(board_items=[("b", OPTIONS["Blocked"], issue("I1"), STALLED, "")])
    _run(fake, tmp_path)
    assert fake.writes == []


def test_a_board_without_a_health_field_gets_no_health_write(tmp_path):
    fake = Fake(board_items=[("b", OPTIONS["Blocked"], issue("I1"), None, "")], health=False)
    _run(fake, tmp_path)
    assert fake.writes == []


# --- The bounds on every write ----------------------------------------------------------


def test_the_board_sync_sends_only_its_three_fixed_writes():
    fake = Fake()
    board = sync.read_board(fake, SETTINGS)
    guard = sync.Guard(board, {"P1"})
    other = sync.ADD.replace("addProjectV2ItemById", "deleteProjectV2Item")
    with pytest.raises(sync.Refused):
        guard.write(fake, other, {"project": PROJECT, "content": "P1"})
    with pytest.raises(sync.Refused):
        guard.write(fake, sync.ADD + "\nmutation { deleteRepository }", {"project": PROJECT, "content": "P1"})
    # A status the sync may set does not carry another write past the guard.
    fake = Fake(board_items=[("item1", None, pr("P1"))])
    guard = sync.Guard(sync.read_board(fake, SETTINGS), {"P1"})
    status = {"project": PROJECT, "item": "item1", "field": FIELD, "option": OPTIONS["Done"]}
    with pytest.raises(sync.Refused):
        guard.write(fake, sync.SET + "\nmutation { deleteRepository }", status)
    assert fake.writes == []


def test_the_board_never_gates():
    """c4: Ready to merge is a label. The sync has no way to merge, approve or write text."""
    fake = Fake()
    board = sync.read_board(fake, SETTINGS)
    guard = sync.Guard(board, {"P1"})
    for query in ("mutation { mergePullRequest }", "mutation { addPullRequestReview }"):
        with pytest.raises(sync.Refused):
            guard.write(fake, query, {"project": PROJECT})
    text = sync.SET.replace("singleSelectOptionId: $option", "text: $option")
    with pytest.raises(sync.Refused):
        guard.write(fake, text, {"project": PROJECT, "item": "x", "field": "PVTF_blocked_by", "option": "a"})
    assert fake.writes == []


def test_the_board_sync_writes_only_to_the_configured_board(tmp_path):
    fake = Fake(open_work=[pr("P1")], project="PVT_someone_elses")
    said = _run(fake, tmp_path)
    assert fake.writes == []
    assert any("not the board the settings name" in line for line in said)
    # The guard refuses another board on its own, not only through the run's first check.
    fake = Fake()
    guard = sync.Guard(sync.read_board(fake, SETTINGS), {"P1"})
    with pytest.raises(sync.Refused):
        guard.write(fake, sync.ADD, {"project": "PVT_someone_elses", "content": "P1"})
    assert fake.writes == []


def test_the_board_sync_writes_only_work_it_read_from_the_board_s_own_repositories(tmp_path):
    fake = Fake()
    board = sync.read_board(fake, SETTINGS)
    guard = sync.Guard(board, {"P1"})
    with pytest.raises(sync.Refused):
        guard.write(fake, sync.ADD, {"project": PROJECT, "content": "P_elsewhere"})
    assert fake.writes == []


def test_the_board_sync_sets_only_the_status_field_to_one_of_its_options_or_health_to_stalled():
    fake = Fake(board_items=[("item1", None, pr("P1"))])
    board = sync.read_board(fake, SETTINGS)
    guard = sync.Guard(board, {"P1"})
    good = {"project": PROJECT, "item": "item1", "field": FIELD, "option": OPTIONS["Done"]}
    flag = {"project": PROJECT, "item": "item1", "field": HEALTH, "option": STALLED}
    clear = {"project": PROJECT, "item": "item1", "field": HEALTH}
    bad = [
        (sync.SET, {**good, "field": "PVTF_title"}),
        (sync.SET, {**good, "option": "opt_made_up"}),
        (sync.SET, {**good, "item": "item_other"}),
        (sync.SET, {**flag, "option": OPTIONS["Done"]}),
        (sync.CLEAR, {**clear, "field": FIELD}),
    ]
    for query, variables in bad:
        with pytest.raises(sync.Refused):
            guard.write(fake, query, variables)
    assert fake.writes == []
    for query, variables in [(sync.SET, good), (sync.SET, flag), (sync.CLEAR, clear)]:
        guard.write(fake, query, variables)
    assert [v for _, v in fake.writes] == [good, flag, clear]


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
    fake = Fake(open_work=[pr("P1")])
    said = _run(fake, tmp_path, dry=True)
    assert fake.writes == []
    assert any("would add" in line and "Waiting for review" in line for line in said)


def test_the_sync_reads_what_the_flow_needs():
    for part in ("headRefOid", "statusCheckRollup { state }", "assignees(first: 1) { totalCount }", "updatedAt"):
        assert part in sync.READ_OPEN and part in sync.READ_BOARD
    assert 'fieldValueByName(name: "Blocked by")' in sync.READ_BOARD
    assert 'field(name: "Health")' in sync.READ_BOARD


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
    config.write_text(f'[tracker]\nowner = "{OWNER}"\nboard = 7\nproject_id = "{PROJECT}"\nowner_merges = []\n')
    assert sync.settings_from(config).owner_merges == ()


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
