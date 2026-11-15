"""The first tracker adapter: one GitHub repository's open pull requests, read only.

It reads through GitHub's command line, the way the bridge already reads pull
requests and check runs, and it refuses to run any command but its two reads.
So it needs no token beyond the one `gh` already has, and changes nothing.

A pull request maps to a state by fixed GitHub facts only, never by its text
(working-model.md 5b), the first that holds winning:

1. a review requested from the repository's owner: it waits on the owner;
2. a check whose latest run failed: stopped;
3. a conflict with the branch it goes into: stopped;
4. nothing changed on it for a day: stopped, derived from its age;
5. otherwise it is moving.

"Changed" is GitHub's `updatedAt`, which also moves on any comment, a bot's included.
So a pull request a bot comments on daily never counts as quiet.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

from claude_voice import software
from claude_voice.tracker.base import Item, State, Tracker

#: A check's latest outcome that stops a pull request.
FAILED = frozenset({"FAILURE", "TIMED_OUT", "STARTUP_FAILURE", "ACTION_REQUIRED", "ERROR"})
#: How long a pull request may sit unchanged before it counts as stopped.
QUIET = timedelta(days=1)
#: The fields read of each open pull request.
FIELDS = "number,title,url,isDraft,mergeable,baseRefName,updatedAt,reviewRequests,statusCheckRollup"
#: The only commands this adapter may run. Both only read.
READS = (("pr", "list"), ("repo", "view"))
#: The most pull requests read in one go.
LIMIT = 100
#: A repository as `gh` takes it: owner/name, plain characters only.
NAME = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})/[A-Za-z0-9._-]{1,100}")

Run = Callable[[list[str]], str | None]


class GitHubError(Exception):
    """GitHub could not be read, said with the step that fixes it."""


def _when(text: object) -> datetime | None:
    try:
        return datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except ValueError:
        return None


def latest_checks(rollup: list[Mapping[str, Any]] | None) -> dict[str, str]:
    """Each check's latest outcome by its name. GitHub keeps older runs of a check beside its newest.

    A cancelled run is skipped, so a newer run cancelled on its way never hides an older failure.
    """
    latest: dict[str, tuple[str, str]] = {}
    for check in rollup or []:
        name = str(check.get("name") or check.get("context") or "a check")
        started = str(check.get("startedAt") or check.get("completedAt") or "")
        outcome = str(check.get("conclusion") or check.get("state") or "").upper()
        # RULE: a cancelled run never hides an older failure
        if outcome == "CANCELLED":
            continue
        if name not in latest or started >= latest[name][0]:
            latest[name] = (started, outcome)
    return {name: outcome for name, (_, outcome) in latest.items()}


def state_of(pr: Mapping[str, Any], owner: str, now: datetime) -> tuple[State, str]:
    """A pull request's state and its one-line why, from fixed GitHub facts only."""
    asked = {str(r.get("login") or "").lower() for r in pr.get("reviewRequests") or []}
    # RULE: a review asked of the owner waits on the owner
    if owner.lower() in asked:
        return "waits_on_owner", "a review is asked of the owner"
    failed = sorted(name for name, outcome in latest_checks(pr.get("statusCheckRollup")).items() if outcome in FAILED)
    # RULE: a failed check stops a pull request
    if failed:
        return "stopped", "a check failed: " + ", ".join(failed[:2]) + (
            f" and {len(failed) - 2} more" if failed[2:] else ""
        )
    # RULE: a conflict with its base stops a pull request
    if pr.get("mergeable") == "CONFLICTING":
        return "stopped", f"it conflicts with {pr.get('baseRefName') or 'its base'}"
    moved = _when(pr.get("updatedAt"))
    # RULE: a pull request quiet for a day is stopped
    if moved is not None and now - moved >= QUIET:
        return "stopped", f"no activity for {int((now - moved).total_seconds() // 3600)} h"
    return "moving", "a draft, no check has failed" if pr.get("isDraft") else "no check has failed and no conflict"


class GitHub(Tracker):
    """One repository's open pull requests, as tracker items. It never writes."""

    def __init__(self, repo: str | None = None, run: Run | None = None, now: Callable[[], datetime] | None = None):
        # RULE: a repository that is not owner and name is refused
        if repo is not None and not NAME.fullmatch(repo):
            raise GitHubError(
                f"{repo!r} is not a repository; name it as owner/name, such as example-owner/example-repo"
            )
        self.repo = repo
        self.capped: int | None = None
        self._run = run or (lambda args: software._gh(args))  # looked up per call, so a test can replace it
        self._now = now or (lambda: datetime.now(UTC))

    def _read(self, *args: str) -> Any:
        # RULE: the github adapter runs only reading commands
        if tuple(args[:2]) not in READS:
            raise GitHubError(f"the tracker only reads, and `gh {' '.join(args[:2])}` is not one of its reads")
        out = self._run(list(args))
        if out is None:
            raise GitHubError(
                f"GitHub's command line gave no answer for {self.repo or 'this folder'}. "
                "Check that it is installed and signed in: gh auth status"
            )
        try:
            return json.loads(out)
        except ValueError as exc:
            raise GitHubError("GitHub's command line answered something that is not JSON; try again") from exc

    def describe(self) -> tuple[str, str]:
        """The repository's full name and its visibility, such as PUBLIC."""
        found = self._read("repo", "view", *([self.repo] if self.repo else []), "--json", "nameWithOwner,visibility")
        self.repo = str(found.get("nameWithOwner") or self.repo or "")
        return self.repo, str(found.get("visibility") or "").upper()

    def items(self, component: str | None = None) -> list[Item]:
        repo = component or self.repo or self.describe()[0]
        owner, now = repo.split("/")[0], self._now()
        prs = self._read("pr", "list", "--repo", repo, "--state", "open", "--limit", str(LIMIT), "--json", FIELDS)
        self.capped = LIMIT if len(prs) >= LIMIT else None
        found = []
        for pr in prs:
            state, why = state_of(pr, owner, now)
            found.append(
                Item(
                    f"#{pr['number']}",
                    str(pr.get("title") or ""),
                    repo,
                    state,
                    why,
                    str(pr.get("url") or ""),
                    _when(pr.get("updatedAt")),
                )
            )
        return found
