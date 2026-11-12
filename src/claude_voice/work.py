"""The team's work on one page: what waits on the owner, and every open pull request.

The manager session writes what waits on the owner to a small file of its own
(``~/.local/state/claude-voice/waiting.json``), a list of items with an ``id``
and a ``text``, and optionally a ``command`` to paste, the ``repo`` or
``session`` it is about, and ``since``. The page reads it fresh each time, so a
step the owner has taken never lingers. Pull requests come from GitHub's
command line (``run``, given by the server), asked at most once a minute,
because the phone looks often. Three days before Claude Code's sign-in runs
out, the bridge adds an item of its own first, saying to type /login.
"""

from __future__ import annotations

import json
import re
import subprocess
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from claude_voice import procs
from claude_voice.config import PluginSpec

WAITING = Path("~/.local/state/claude-voice/waiting.json")
PR_SECONDS = 60.0  # how long a look at the pull requests is kept
LOGIN_SECONDS = 3600.0  # how long a look at Claude Code's sign-in date is kept
LOGIN_WARNING = timedelta(days=3)  # how long before the sign-in runs out the owner is told
GITHUB = re.compile(r"github\.com[:/]([\w.-]+)/([\w.-]+?)(?:\.git)?/?$")
FAILED = {"FAILURE", "CANCELLED", "TIMED_OUT", "ACTION_REQUIRED", "STARTUP_FAILURE", "ERROR"}
PASSED = {"SUCCESS", "SKIPPED", "NEUTRAL"}


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def waiting(path: Path | None = None) -> list[dict[str, Any]]:
    """What waits on the owner, as the manager wrote it; an item without an id and a text is left out."""
    try:
        raw = json.loads((path or WAITING).expanduser().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    items = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict) or not _text(item.get("id")) or not _text(item.get("text")):
            continue
        items.append(
            {
                "id": item["id"],
                "text": item["text"],
                "command": _text(item.get("command")),
                "since": _text(item.get("since")) or _now(),
                "repo": _text(item.get("repo")),
                "session": _text(item.get("session")),
            }
        )
    return items


def _claude() -> str | None:
    """Claude Code's command line: the owner's own first, since the copy bundled with the SDK
    may be too old to say when the sign-in runs out. Both share the one sign-in."""
    import claude_agent_sdk

    bundled = Path(claude_agent_sdk.__file__).parent / "_bundled" / "claude"
    return procs.find("claude") or (str(bundled) if bundled.exists() else None)


def read_login_expiry() -> datetime | None:
    """When Claude Code's sign-in runs out, as `claude auth status` says; None when it cannot say.
    The date comes from Claude Code itself, so no file holding a secret is opened."""
    cli = _claude()
    if not cli:
        return None
    try:
        out = subprocess.run([cli, "auth", "status"], capture_output=True, text=True, timeout=30, check=False)
        when = datetime.fromisoformat(json.loads(out.stdout)["loginExpiresAt"])
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        return None
    return when if when.tzinfo else when.replace(tzinfo=UTC)


def login_item(expires: datetime | None, now: datetime) -> dict[str, Any] | None:
    """The item that tells the owner to sign in again, or None while the sign-in has days left."""
    # RULE: a sign-in that runs out within three days waits on the owner
    if expires is None or expires - now > LOGIN_WARNING:
        return None
    when = "has run out" if expires <= now else f"runs out on {expires.date().isoformat()}"
    return {
        "id": "claude-login",
        "text": f"Claude Code's sign-in {when}. Run claude and type /login to sign in again.",
        "command": "claude",
        "since": (expires - LOGIN_WARNING).isoformat(timespec="seconds"),  # stable, so the page changes only once
        "repo": None,
        "session": None,
    }


def checks(rollup: list[dict[str, Any]]) -> str:
    """A pull request's checks in one word: fail if any failed, pending if any still runs, else pass."""
    if not rollup:
        return "pending"
    words = [str(c.get("conclusion") or c.get("state") or "").upper() for c in rollup]
    running = any(c.get("status") not in (None, "COMPLETED") for c in rollup)
    if any(w in FAILED for w in words):
        return "fail"
    if running or not all(w in PASSED for w in words):
        return "pending"
    return "pass"


def open_prs(repos: dict[str, str], run: Callable[[list[str]], str | None]) -> tuple[list[dict[str, Any]], list[str]]:
    """Every open pull request in these repositories, and the names of those that could not be read."""
    prs: list[dict[str, Any]] = []
    unreadable = []
    for name, repo in repos.items():
        listed = run(["pr", "list", "--repo", repo, "--state", "open", "--json",
                      "number,title,url,isDraft,statusCheckRollup"])  # fmt: skip
        try:
            found = json.loads(listed) if listed is not None else None
        except ValueError:
            found = None
        if not isinstance(found, list):
            unreadable.append(name)
            continue
        prs += [
            {
                "repo": name,
                "number": pr["number"],
                "title": pr["title"],
                "url": pr["url"],
                "checks": checks(pr.get("statusCheckRollup") or []),
                "draft": bool(pr.get("isDraft")),
            }
            for pr in found
        ]
    return prs, unreadable


def _git(folder: Path, *args: str) -> str | None:
    try:
        out = subprocess.run(["git", "-C", str(folder), *args], capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() if out.returncode == 0 else None


def plugin_repos(plugins: list[PluginSpec]) -> list[tuple[str, str, Path]]:
    """Each plugin's repository on GitHub, as its checked-out branch pushes to: (name, owner/name, folder)."""
    found = []
    for spec in plugins:
        folder = Path(spec.cwd)
        branch = _git(folder, "branch", "--show-current")
        remote = (branch and _git(folder, "config", f"branch.{branch}.remote")) or "origin"
        match = GITHUB.search(_git(folder, "remote", "get-url", remote) or "")
        if match:
            found.append((match[2], f"{match[1]}/{match[2]}", folder))
    return found


class Board:
    """The work page's body, with the pull requests kept a minute and the owner's list read fresh."""

    def __init__(
        self,
        repos: dict[str, str],
        run: Callable[[list[str]], str | None],
        waiting_file: Path | None = None,
        clock: Callable[[], float] = time.monotonic,
        login_expiry: Callable[[], datetime | None] | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.repos = repos
        self.waiting_file = waiting_file
        self.run = run
        self.clock = clock
        self.login_expiry, self.now = login_expiry, now
        self._login: tuple[float, datetime | None] | None = None
        self._prs: tuple[float, list[dict[str, Any]], list[str]] | None = None
        self._last: dict[str, Any] | None = None

    def report(self) -> dict[str, Any]:
        if self._prs is None or self.clock() - self._prs[0] >= PR_SECONDS:
            self._prs = (self.clock(), *open_prs(self.repos, self.run))
        body = {
            "waiting_on_owner": self._sign_in() + waiting(self.waiting_file),
            "open_prs": self._prs[1],
            "unreadable": self._prs[2],
        }
        self._last = body
        return {"as_of": _now(), **body}

    def _sign_in(self) -> list[dict[str, Any]]:
        """The sign-in item when it is due, its date asked of Claude Code at most once an hour."""
        if self.login_expiry is None:
            return []
        if self._login is None or self.clock() - self._login[0] >= LOGIN_SECONDS:
            self._login = (self.clock(), self.login_expiry())
        item = login_item(self._login[1], self.now())
        return [item] if item else []

    def changed(self) -> dict[str, Any] | None:
        """The page's body when it differs from the last one seen, for the stream; otherwise None."""
        before = self._last
        now = self.report()
        return None if before is not None and {k: v for k, v in now.items() if k != "as_of"} == before else now
