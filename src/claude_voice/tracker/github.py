"""Keep a GitHub project board in step with the open pull requests and issues of its repositories.

`claude-voice tracker sync` runs this every five minutes from a systemd user timer, as the
owner, with the owner's own `gh` login. It runs outside the bridge, so a slow or failed run
never touches what the voice and the phone reach.

The board only mirrors the work; nothing waits on it. A run reads the board and the open
work in two queries, then sends only what changed. It writes no text. It adds a pull request
or issue that already exists to the board, sets its Status, and sets or clears its Health.
Each write passes three bounds first:
- the query is one of the three fixed mutations below, nothing assembled;
- the board is the one the settings name, by its node id;
- the work, the field and the option all come from this run's own reads, of the
  repositories linked to that board. The field is Status, or Health with its one option.

Which column a piece of work belongs in is decided in `flow`, from facts: whether it is a
draft or merged, the reviewers' verdict files at its current head (read in `verdicts`), its
checks, and who is assigned. Backlog, To do and Blocked are moved by hand, and so is an
owner's issue put in the waiting column. The sync leaves those where they are. Every other
column is the sync's, and a hand move into one is put right on the next run.

Health is Stalled when work has gone quiet, and when an item is in Blocked with no reason in
its "Blocked by" field. The sync never writes that reason. It never sends a write that
changes nothing either. GitHub keeps the time a Status last changed, so a write of the same
value would reset the time an item has spent in its column.

The waiting column's name is a setting, since a board names its owner there. Under a
rate-limit floor a run does nothing, and says so once, so it never starves the sessions
that share the same login. Design: board-flow-v1.
"""

from __future__ import annotations

import json
import subprocess
import time
import tomllib
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import flow, verdicts

#: The GraphQL points a run leaves for everyone else on the same login.
FLOOR = 500
#: The column for each kind of work; `waiting` is renamed in the settings.
NAMES = {
    "backlog": "Backlog",
    "todo": "To do",
    "progress": "In progress",
    "review": "Waiting for review",
    "reviewing": "Being reviewed",
    "changes": "Changes requested",
    "ready": "Ready to merge",
    "waiting": "Waiting on owner",
    "blocked": "Blocked",
    "done": "Done",
}
#: The tiers whose ready pull requests wait on the owner rather than in Ready to merge.
OWNER_MERGES = ("risky",)
HEALTH = "Health"
STALLED = "Stalled"

_WORK = """
      ... on PullRequest {
        __typename id number state isDraft headRefOid updatedAt repository { nameWithOwner }
        author { login } commits(last: 1) { nodes { commit { oid statusCheckRollup { state } } } }
      }
      ... on Issue {
        __typename id state updatedAt repository { nameWithOwner } author { login }
        labels(first: 1) { totalCount } comments(last: 50) { nodes { author { login } } }
        assignees(first: 1) { totalCount } closedByPullRequestsReferences(first: 1) { totalCount }
      }"""

READ_BOARD = (
    """query($owner: String!, $number: Int!, $after: String) {
  rateLimit { remaining }
  user(login: $owner) {
    projectV2(number: $number) {
      id
      status: field(name: "Status") { ... on ProjectV2SingleSelectField { id options { id name } } }
      health: field(name: "Health") { ... on ProjectV2SingleSelectField { id options { id name } } }
      repositories(first: 100) { nodes { nameWithOwner } }
      items(first: 100, after: $after) {
        pageInfo { hasNextPage endCursor }
        nodes {
          id
          status: fieldValueByName(name: "Status") { ... on ProjectV2ItemFieldSingleSelectValue { optionId } }
          health: fieldValueByName(name: "Health") { ... on ProjectV2ItemFieldSingleSelectValue { optionId } }
          reason: fieldValueByName(name: "Blocked by") { ... on ProjectV2ItemFieldTextValue { text } }
          content {"""
    + _WORK
    + """
          }
        }
      }
    }
  }
}"""
)

READ_OPEN = (
    """query($q: String!, $after: String) {
  search(query: $q, type: ISSUE, first: 100, after: $after) {
    pageInfo { hasNextPage endCursor }
    nodes {"""
    + _WORK
    + """
    }
  }
}"""
)

ADD = """mutation($project: ID!, $content: ID!) {
  addProjectV2ItemById(input: {projectId: $project, contentId: $content}) { item { id } }
}"""

SET = """mutation($project: ID!, $item: ID!, $field: ID!, $option: String!) {
  updateProjectV2ItemFieldValue(
    input: {projectId: $project, itemId: $item, fieldId: $field, value: {singleSelectOptionId: $option}}
  ) { projectV2Item { id } }
}"""

CLEAR = """mutation($project: ID!, $item: ID!, $field: ID!) {
  clearProjectV2ItemFieldValue(input: {projectId: $project, itemId: $item, fieldId: $field}) {
    projectV2Item { id }
  }
}"""

Client = Callable[[str, dict[str, Any]], dict[str, Any]]


class Refused(Exception):
    """A write outside the sync's bounds; nothing was sent."""


class SyncError(Exception):
    """GitHub could not be asked, or answered with an error."""


@dataclass(frozen=True)
class Settings:
    owner: str
    number: int
    project_id: str
    names: dict[str, str] = field(default_factory=lambda: dict(NAMES))
    owner_merges: tuple[str, ...] = OWNER_MERGES


@dataclass
class Item:
    id: str
    status: str | None  # option id
    health: str | None  # option id
    reason: str  # the "Blocked by" text


@dataclass
class Board:
    project: str
    field: str
    options: dict[str, str]  # column name -> option id
    repos: set[str]
    items: dict[str, Item]  # content id -> the board's item for it
    work: list[dict[str, Any]]
    remaining: int
    health: str | None = None  # the Health field's id, when the board has one
    stalled: str | None = None  # its Stalled option's id


def settings_from(path: Path) -> Settings | None:
    """The `[tracker]` table of the bridge's config file, or None when it names no board."""
    if not path.is_file():
        return None
    with path.open("rb") as f:
        table = tomllib.load(f).get("tracker") or {}
    if not table.get("board"):
        return None
    names = dict(NAMES)
    if table.get("waiting"):
        names["waiting"] = str(table["waiting"])
    merges = tuple(str(t) for t in table.get("owner_merges", OWNER_MERGES))
    return Settings(str(table["owner"]), int(table["board"]), str(table["project_id"]), names, merges)


def gh_graphql(query: str, variables: dict[str, Any], run: Callable[..., Any] = subprocess.run) -> dict[str, Any]:
    """Ask GitHub with the owner's own `gh` login; query and variables go in one body on stdin."""
    body = json.dumps({"query": query, "variables": variables})
    done = run(["gh", "api", "graphql", "--input", "-"], input=body, capture_output=True, text=True, timeout=60)
    if done.returncode:
        raise SyncError(f"gh api graphql failed: {done.stderr.strip()[-500:]}")
    answer = json.loads(done.stdout)
    if answer.get("errors"):
        raise SyncError(f"GitHub answered with errors: {json.dumps(answer['errors'])[:500]}")
    data: dict[str, Any] = answer["data"]
    return data


def _pages(client: Client, query: str, variables: dict[str, Any], where: Callable[[dict[str, Any]], dict[str, Any]]):
    after = None
    while True:
        data = client(query, {**variables, "after": after})
        part = where(data)
        yield data, part
        if not part["pageInfo"]["hasNextPage"]:
            return
        after = part["pageInfo"]["endCursor"]


def _new_board(data: dict[str, Any]) -> Board:
    project = data["user"]["projectV2"]
    status = project["status"]
    health = project.get("health") or {}
    stalled = next((o["id"] for o in health.get("options") or () if o["name"] == STALLED), None)
    return Board(
        project=project["id"],
        field=status["id"],
        options={o["name"]: o["id"] for o in status["options"]},
        repos={r["nameWithOwner"] for r in project["repositories"]["nodes"]},
        items={},
        work=[],
        remaining=int(data["rateLimit"]["remaining"]),
        health=health.get("id") if stalled else None,
        stalled=stalled,
    )


def read_board(client: Client, settings: Settings) -> Board:
    """The board: its id, its Status and Health fields and options, its repositories and items."""
    board: Board | None = None
    for data, items in _pages(
        client,
        READ_BOARD,
        {"owner": settings.owner, "number": settings.number},
        lambda d: d["user"]["projectV2"]["items"],
    ):
        board = board or _new_board(data)
        for node in items["nodes"]:
            content = node.get("content") or {}
            if content.get("id") and content.get("repository"):
                status = (node.get("status") or {}).get("optionId")
                health = (node.get("health") or {}).get("optionId")
                reason = str((node.get("reason") or {}).get("text") or "")
                board.items[content["id"]] = Item(node["id"], status, health, reason)
                board.work.append(content)
    assert board is not None
    return board


def read_open(client: Client, settings: Settings) -> list[dict[str, Any]]:
    """Every open pull request and issue in the owner's repositories, in pages of 100."""
    found: list[dict[str, Any]] = []
    query = f"user:{settings.owner} is:open archived:false"
    for _, part in _pages(client, READ_OPEN, {"q": query}, lambda d: d["search"]):
        found += [n for n in part["nodes"] if n.get("id") and n.get("repository")]
    return found


@dataclass(frozen=True)
class Write:
    query: str
    variables: dict[str, Any]
    said: str


def column(work: dict[str, Any], now: str | None, settings: Settings, stamps: Sequence[verdicts.Verdict] = ()) -> str:
    """Which kind of column a piece of work belongs in, from its own facts and where it is now."""
    if work["__typename"] == "PullRequest":
        return flow.pr_column(work, now, stamps, settings.owner_merges)
    return flow.issue_column(work, now, settings.owner)


def _health(board: Board, item: Item, stalled: bool, where: str) -> Write | None:
    """The Health write an item needs, or None when it is already right."""
    if board.health is None:
        return None
    flagged = item.health == board.stalled
    # RULE: an item already flagged stalled gets no write
    if stalled == flagged:
        return None
    if stalled:
        return Write(SET, {"item": item.id, "field": board.health, "option": board.stalled}, f"flag {where} Stalled")
    return Write(CLEAR, {"item": item.id, "field": board.health}, f"clear Stalled on {where}")


def _item_writes(
    board: Board,
    settings: Settings,
    kinds: dict[str, str],
    content: str,
    work: dict[str, Any],
    stamps: Sequence[verdicts.Verdict],
    clock: float,
) -> list[Write]:
    """The writes one piece of work needs: added with its Status, or its Status and Health set."""
    held = board.items.get(content)
    kind = column(work, kinds.get(held.status) if held and held.status else None, settings, stamps)
    name = settings.names[kind]
    option = board.options.get(name)
    if option is None:
        return []  # the board has no such column; nothing to set
    where = f"{work['repository']['nameWithOwner']} {work['__typename']} {content}"
    if held is None:
        return (
            [Write(ADD, {"content": content, "option": option}, f"add {where} as {name}")]
            if work["state"] == "OPEN"
            else []
        )
    writes = []
    # RULE: an unchanged status makes no write
    if held.status != option:
        writes.append(Write(SET, {"item": held.id, "field": board.field, "option": option}, f"set {where} to {name}"))
    health = _health(board, held, flow.stalled(work, kind, stamps, held.reason, clock), where)
    return writes + ([health] if health else [])


def plan(
    board: Board,
    open_work: Iterable[dict[str, Any]],
    settings: Settings,
    reading: verdicts.Reading | None = None,
    clock: float | None = None,
) -> list[Write]:
    """The writes that bring the board in step: only what differs."""
    files = reading.files if reading else {}
    clock = time.time() if clock is None else clock
    seen: dict[str, dict[str, Any]] = {}
    for work in [*board.work, *open_work]:
        # RULE: an item from a repository outside the board is never touched
        if work["repository"]["nameWithOwner"] in board.repos:
            seen[work["id"]] = work
    kinds = {board.options[name]: kind for kind, name in settings.names.items() if name in board.options}
    writes: list[Write] = []
    for content, work in seen.items():
        repo = str(work["repository"]["nameWithOwner"]).rpartition("/")[2]
        stamps = files.get((repo, int(work.get("number") or 0)), [])
        writes += _item_writes(board, settings, kinds, content, work, stamps, clock)
    return writes


class Guard:
    """The one way a write reaches GitHub: each is checked against this run's own reads first."""

    def __init__(self, board: Board, contents: set[str]) -> None:
        self.board = board
        self.contents = contents
        self.items = {item.id for item in board.items.values()}

    def write(self, client: Client, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        # RULE: the board sync sends only its three fixed writes
        if query not in (ADD, SET, CLEAR):
            raise Refused("not one of the sync's three writes")
        # RULE: the board sync writes only to the configured board
        if variables.get("project") != self.board.project:
            raise Refused("not the board the settings name")
        # RULE: the board sync writes only work it read from the board's own repositories
        if query == ADD and variables.get("content") not in self.contents:
            raise Refused("work this run did not read from the board's repositories")
        if query != ADD:
            self._field(query, variables)
        answer = client(query, variables)
        if query == ADD:
            self.items.add(answer["addProjectV2ItemById"]["item"]["id"])
        return answer

    def _field(self, query: str, variables: dict[str, Any]) -> None:
        board, chosen, option = self.board, variables.get("field"), variables.get("option")
        status = chosen == board.field and option in board.options.values()
        health = board.health is not None and chosen == board.health
        # RULE: the board sync sets only the status field to one of its options or health to stalled
        if not (status or (health and (query == CLEAR or option == board.stalled))) or (query == CLEAR and not health):
            raise Refused("not Status with one of its options, nor Health's Stalled")
        if variables.get("item") not in self.items:
            raise Refused("an item this run did not read or add")


def _low(state: Path, low: bool) -> bool:
    """Note whether the rate limit is low; True only when that just began."""
    path = state / "board-sync.json"
    try:
        was = bool(json.loads(path.read_text()).get("low"))
    except (OSError, ValueError):
        was = False
    if was != low:
        state.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"low": low}))
    return low and not was


def _send(client: Client, guard: Guard, board: Board, w: Write) -> None:
    if w.query == ADD:
        added = guard.write(client, ADD, {"project": board.project, "content": w.variables["content"]})
        item = added["addProjectV2ItemById"]["item"]["id"]
        guard.write(
            client, SET, {"project": board.project, "item": item, "field": board.field, "option": w.variables["option"]}
        )
        return
    guard.write(client, w.query, {"project": board.project, **w.variables})


def sync(client: Client, settings: Settings, state: Path, say: Callable[[str], None] = print, dry: bool = False) -> int:
    """One run: read, compare, and send only what changed. Never fails on a low rate limit."""
    board = read_board(client, settings)
    if board.project != settings.project_id:
        say(f"board {settings.number} has id {board.project}, not the board the settings name; nothing written")
        return 0
    # RULE: a low rate limit skips the run and says so once
    if board.remaining < FLOOR:
        if _low(state, True):
            say(f"rate limit low ({board.remaining} points left, floor {FLOOR}); skipping until it recovers")
        return 0
    _low(state, False)
    open_work = read_open(client, settings)
    short = {repo.rpartition("/")[2] for repo in board.repos}
    reading = verdicts.read(state / "reviews", settings.owner, short)
    if reading.ignored:
        say(f"ignored {reading.ignored} verdict file(s) that were not a reviewer's plain, well-formed verdict")
    writes = plan(board, open_work, settings, reading)
    read = [*board.work, *open_work]
    guard = Guard(board, {w["id"] for w in read if w["repository"]["nameWithOwner"] in board.repos})
    for w in writes:
        say(("would " if dry else "") + w.said)
        if not dry:
            _send(client, guard, board, w)
    return 0
