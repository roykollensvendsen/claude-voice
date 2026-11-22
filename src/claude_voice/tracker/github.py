"""Keep a GitHub project board in step with the open pull requests and issues of its repositories.

`claude-voice tracker sync` runs this every five minutes from a systemd user timer, as the
owner, with the owner's own `gh` login. It runs outside the bridge, so a slow or failed run
never touches what the voice and the phone reach.

The board only mirrors the work; nothing waits on it. A run reads the board and the open
work in two queries, then sends only what changed. It writes no text: it adds a pull request
or issue that already exists to the board, and sets its Status. Each write passes three
bounds first:
- the query is one of the two fixed mutations below, nothing assembled;
- the board is the one the settings name, by its node id;
- the work, the field and the option all come from this run's own reads, of the
  repositories linked to that board.

What a piece of work becomes:
- a draft pull request, or one nobody has been asked to review: In progress;
- a pull request with a review asked for or given on GitHub, or with a reviewer's verdict file
  in the bridge's state folder (`reviews/<repo>-<number>.<reviewer>.json`): In review;
- merged or closed, a pull request or an issue: Done;
- an open issue with someone assigned: In progress, whoever opened it. A session assigns an
  issue the moment it starts on it, so the board shows the work while it runs;
- any other open issue: Backlog, or the waiting column when someone other than the owner opened it
  and the owner has neither labelled nor answered it yet. An open issue moved by hand stays
  where it was put, except in In progress: once nobody is assigned, it goes back to Backlog.

The waiting column's name is a setting, since a board names its owner there. Under a
rate-limit floor a run does nothing, and says so once, so it never starves the sessions
that share the same login.
"""

from __future__ import annotations

import json
import re
import stat
import subprocess
import tomllib
from collections.abc import Callable, Iterable
from collections.abc import Set as AbstractSet
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: The GraphQL points a run leaves for everyone else on the same login.
FLOOR = 500
#: The column for each kind of work; `waiting` is renamed in the settings.
NAMES = {
    "progress": "In progress",
    "review": "In review",
    "done": "Done",
    "backlog": "Backlog",
    "waiting": "Waiting on owner",
}

_WORK = """
      ... on PullRequest {
        __typename id number state isDraft repository { nameWithOwner } author { login }
        reviewRequests(first: 1) { totalCount } reviews(first: 1) { totalCount }
      }
      ... on Issue {
        __typename id state repository { nameWithOwner } author { login }
        labels(first: 1) { totalCount } comments(last: 50) { nodes { author { login } } }
        assignees(first: 1) { totalCount }
      }"""

READ_BOARD = (
    """query($owner: String!, $number: Int!, $after: String) {
  rateLimit { remaining }
  user(login: $owner) {
    projectV2(number: $number) {
      id
      field(name: "Status") { ... on ProjectV2SingleSelectField { id options { id name } } }
      repositories(first: 100) { nodes { nameWithOwner } }
      items(first: 100, after: $after) {
        pageInfo { hasNextPage endCursor }
        nodes {
          id
          fieldValueByName(name: "Status") { ... on ProjectV2ItemFieldSingleSelectValue { optionId } }
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


@dataclass
class Board:
    project: str
    field: str
    options: dict[str, str]  # column name -> option id
    repos: set[str]
    items: dict[str, tuple[str, str | None]]  # content id -> (item id, option id)
    work: list[dict[str, Any]]
    remaining: int


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
    return Settings(str(table["owner"]), int(table["board"]), str(table["project_id"]), names)


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


def read_board(client: Client, settings: Settings) -> Board:
    """The board: its id, its Status field and options, its repositories and its items."""
    board: Board | None = None
    for data, items in _pages(
        client,
        READ_BOARD,
        {"owner": settings.owner, "number": settings.number},
        lambda d: d["user"]["projectV2"]["items"],
    ):
        project = data["user"]["projectV2"]
        if board is None:
            status = project["field"]
            board = Board(
                project=project["id"],
                field=status["id"],
                options={o["name"]: o["id"] for o in status["options"]},
                repos={r["nameWithOwner"] for r in project["repositories"]["nodes"]},
                items={},
                work=[],
                remaining=int(data["rateLimit"]["remaining"]),
            )
        for node in items["nodes"]:
            content = node.get("content") or {}
            if content.get("id") and content.get("repository"):
                value = node.get("fieldValueByName") or {}
                board.items[content["id"]] = (node["id"], value.get("optionId"))
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


#: A reviewer's verdict file, and nothing looser: <repo>-<number>.<reviewer>.json.
VERDICT = re.compile(r"^(?P<repo>[A-Za-z0-9._-]+)-(?P<number>[0-9]+)\.(?P<reviewer>[a-z0-9-]+)\.json$")


def stamped(reviews: Path, repos: AbstractSet[str]) -> set[tuple[str, int]]:
    """The pull requests a reviewer has written a verdict for, by repository name and number.

    Only the name counts; no file is read. A name that is not strictly a verdict file's, a
    repository not on the board, and anything but a plain file (a link, say) are ignored.
    """
    found = set()
    for path in reviews.iterdir() if reviews.is_dir() else ():
        match = VERDICT.match(path.name)
        if match and match["repo"] in repos and stat.S_ISREG(path.lstat().st_mode):
            found.add((match["repo"], int(match["number"])))
    return found


def column(work: dict[str, Any], owner: str, verdicts: AbstractSet[tuple[str, int]] = frozenset()) -> str:
    """Which kind of column a piece of work belongs in, from its own facts."""
    if work["state"] != "OPEN":
        return "done"
    if work["__typename"] == "PullRequest":
        return _pr_column(work, verdicts)
    # RULE: an open issue someone has taken is in progress
    if (work.get("assignees") or {}).get("totalCount"):
        return "progress"
    author = (work.get("author") or {}).get("login")
    answered = any((c.get("author") or {}).get("login") == owner for c in work["comments"]["nodes"])
    if author != owner and not work["labels"]["totalCount"] and not answered:
        return "waiting"
    return "backlog"


def _pr_column(work: dict[str, Any], verdicts: AbstractSet[tuple[str, int]]) -> str:
    """An open pull request: in review once a review was asked for, given or stamped."""
    if work["isDraft"]:
        return "progress"
    repo = work["repository"]["nameWithOwner"].split("/", 1)[-1]
    asked = work["reviewRequests"]["totalCount"] or work["reviews"]["totalCount"]
    return "review" if asked or (repo, work.get("number")) in verdicts else "progress"


@dataclass(frozen=True)
class Write:
    query: str
    variables: dict[str, Any]
    said: str


def plan(
    board: Board,
    open_work: Iterable[dict[str, Any]],
    settings: Settings,
    verdicts: AbstractSet[tuple[str, int]] = frozenset(),
) -> list[Write]:
    """The writes that bring the board in step: only what differs."""
    seen: dict[str, dict[str, Any]] = {}
    for work in [*board.work, *open_work]:
        # RULE: an item from a repository outside the board is never touched
        if work["repository"]["nameWithOwner"] in board.repos:
            seen[work["id"]] = work
    writes: list[Write] = []
    waiting = board.options.get(settings.names["waiting"])
    progress = board.options.get(settings.names["progress"])
    for content, work in seen.items():
        kind = column(work, settings.owner, verdicts)
        name = settings.names[kind]
        option = board.options.get(name)
        if option is None:
            continue  # the board has no such column; nothing to set
        where = f"{work['repository']['nameWithOwner']} {work['__typename']} {content}"
        if content in board.items:
            item, now = board.items[content]
            if now == option:
                continue
            # An open issue the owner placed by hand stays, unless it leaves the waiting column,
            # or sits in In progress with nobody assigned any more.
            # RULE: a dropped issue leaves in progress
            if work["__typename"] == "Issue" and kind == "backlog" and now not in (None, waiting, progress):
                continue
            writes.append(Write(SET, {"item": item, "option": option}, f"set {where} to {name}"))
        elif work["state"] == "OPEN":
            writes.append(Write(ADD, {"content": content, "option": option}, f"add {where} as {name}"))
    return writes


class Guard:
    """The one way a write reaches GitHub: each is checked against this run's own reads first."""

    def __init__(self, board: Board, contents: set[str]) -> None:
        self.board = board
        self.contents = contents
        self.items = {item for item, _ in board.items.values()}

    def write(self, client: Client, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        # RULE: the board sync sends only its two fixed writes
        if query not in (ADD, SET):
            raise Refused("not one of the sync's two writes")
        # RULE: the board sync writes only to the configured board
        if variables.get("project") != self.board.project:
            raise Refused("not the board the settings name")
        # RULE: the board sync writes only work it read from the board's own repositories
        if query == ADD and variables.get("content") not in self.contents:
            raise Refused("work this run did not read from the board's repositories")
        if query == SET:
            # RULE: the board sync sets only the status field to one of its options
            if variables.get("field") != self.board.field or variables.get("option") not in self.board.options.values():
                raise Refused("not the Status field, or not one of its options")
            if variables.get("item") not in self.items:
                raise Refused("an item this run did not read or add")
        answer = client(query, variables)
        if query == ADD:
            self.items.add(answer["addProjectV2ItemById"]["item"]["id"])
        return answer


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
    short = {repo.split("/", 1)[-1] for repo in board.repos}
    writes = plan(board, open_work, settings, stamped(state / "reviews", short))
    read = [*board.work, *open_work]
    guard = Guard(board, {w["id"] for w in read if w["repository"]["nameWithOwner"] in board.repos})
    for w in writes:
        say(("would " if dry else "") + w.said)
        if dry:
            continue
        if w.query == ADD:
            added = guard.write(client, ADD, {"project": board.project, "content": w.variables["content"]})
            item = added["addProjectV2ItemById"]["item"]["id"]
        else:
            item = w.variables["item"]
        guard.write(
            client, SET, {"project": board.project, "item": item, "field": board.field, "option": w.variables["option"]}
        )
    return 0
