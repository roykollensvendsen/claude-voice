"""Update claude-voice in place: pull, install, test, restart, and roll back if anything fails.

It runs as its own process, started by the bridge with `systemd-run --user`, because
it restarts the bridge's service and must outlive the bridge doing so. Every step is
written to a small JSON log as it happens; the bridge, old and new, reads that log to
answer GET /api/v1/update and to tell open streams how it goes.

The rules, in order:
- local changes to tracked files stop it before anything is touched;
- an install already up to date is left alone;
- only a fast-forward is pulled, never a merge;
- if installing or the tests fail, the folder goes back to the version that ran,
  and the service is not restarted;
- if the new version does not come back healthy, the old one is put back,
  installed and started again.

Run: python -m claude_voice.updater --repo <clone> --log <file> [--port 8811]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import procs

LOG = Path("~/.local/state/claude-voice/update.json")
SERVICE = "claude-voice"
HEALTHY_WITHIN = 60.0


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


# The bridge runs as a systemd user service, which cannot reach the owner's ssh-agent. So the
# updater's own git commands read GitHub's ssh addresses over HTTPS: a public repository needs
# no key at all. Only these commands; the owner's remotes and terminals keep using ssh.
OVER_HTTPS = ("-c", "url.https://github.com/.insteadOf=git@github.com:")


def _git(repo: Path, *args: str) -> tuple[bool, str]:
    git = procs.find("git") or "git"
    try:
        # RULE: the updater reaches github over https not the owners ssh key
        command = [git, *OVER_HTTPS, "-C", str(repo), *args]
        out = subprocess.run(command, capture_output=True, text=True, timeout=120, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        return False, str(exc)
    return out.returncode == 0, (out.stdout or out.stderr).strip()


class Log:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.body: dict[str, Any] = {"running": True, "pid": os.getpid(), "started_at": _now(), "steps": []}
        self.write()

    def write(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.body))
        tmp.replace(self.path)

    def step(self, step: str, ok: bool, detail: str) -> None:
        self.body["steps"].append({"step": step, "ok": ok, "detail": detail[-400:], "at": _now()})
        self.write()

    def finish(self, result: str, detail: str) -> dict[str, Any]:
        self.body.update(running=False, result=result, detail=detail, finished_at=_now())
        self.write()
        return {"result": result, "detail": detail}


def running(path: Path = LOG) -> bool:
    """Whether an update is under way: its log says so and its process is alive."""
    try:
        body = json.loads(path.expanduser().read_text())
    except (OSError, ValueError):
        return False
    return bool(body.get("running")) and procs.alive(int(body.get("pid") or 0))


def run(
    repo: Path,
    log_path: Path,
    install: Callable[[Path], tuple[bool, str]],
    tests: Callable[[Path], tuple[bool, str]],
    restart: Callable[[], None],
    healthy: Callable[[], bool],
) -> dict[str, Any]:
    log = Log(log_path)
    # RULE: local changes stop the update before anything is touched
    ok, changed = _git(repo, "status", "--porcelain", "--untracked-files=no")
    if not ok or changed:
        log.step("check", False, changed or "git status failed")
        return log.finish("stopped", "There are local changes; commit or stash them, then update again.")
    _, before = _git(repo, "rev-parse", "HEAD")
    log.body["from"] = before
    log.step("check", True, f"no local changes at {before[:7]}")

    ok, out = _git(repo, "fetch", "--quiet")
    _, behind = _git(repo, "rev-list", "--count", "HEAD..@{upstream}")
    if not ok:
        log.step("fetch", False, out)
        return log.finish("stopped", fetch_failure(out))
    log.step("fetch", True, f"{behind or 0} new commits")
    if (behind or "0") == "0":
        log.step("done", True, "already up to date")
        return log.finish("up_to_date", "Already the latest version.")

    ok, out = _git(repo, "merge", "--ff-only", "@{upstream}")
    if not ok:
        log.step("pull", False, out)
        return log.finish("stopped", "The local branch has moved apart from the project's; update it by hand.")
    _, after = _git(repo, "rev-parse", "HEAD")
    log.body["to"] = after
    log.step("pull", True, f"{before[:7]} → {after[:7]}")

    def roll_back(reason: str, restarted: bool) -> dict[str, Any]:
        ok, out = _git(repo, "reset", "--hard", before)
        install(repo)
        if restarted:
            restart()
            back = healthy()
            log.step("rollback", ok and back, f"back on {before[:7]}; {'healthy' if back else 'NOT healthy'}")
        else:
            log.step("rollback", ok, f"back on {before[:7]}")
        return log.finish("rolled_back", reason)

    for step, action in (("install", install), ("test", tests)):
        ok, out = action(repo)
        log.step(step, ok, out)
        # RULE: an update whose tests fail is rolled back without a restart
        if not ok:  # roll back
            return roll_back(f"The new version failed to {step}; the old one is kept.", restarted=False)

    log.step("restart", True, "the voice drops for a few seconds")
    restart()
    if not healthy():
        log.step("verify", False, "the bridge did not answer after the restart")
        return roll_back("The new version did not start; the old one is back.", restarted=True)
    log.step("verify", True, "the bridge answers")
    log.step("done", True, f"now on {after[:7]}")
    return log.finish("updated", f"Updated to {after[:7]}.")


# -- the real computer ---------------------------------------------------------------


REFUSED = ("Permission denied", "Authentication failed", "could not read Username", "Repository not found")


def fetch_failure(output: str) -> str:
    """What to tell the owner when fetching the latest version failed."""
    if any(sign in output for sign in REFUSED):
        return (
            "Could not fetch the latest version: GitHub refused. The bridge reads GitHub over HTTPS "
            "without a key, which works for a public repository; a private repository or fork needs "
            "its own read-only access set up for the bridge's service."
        )
    return "Could not fetch the latest version."


def _uv(repo: Path, *args: str) -> tuple[bool, str]:
    uv = procs.find("uv")
    if not uv:
        return False, "uv is not installed"
    try:
        out = subprocess.run([uv, *args], cwd=repo, capture_output=True, text=True, timeout=900, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        return False, str(exc)
    tail = (out.stdout + out.stderr).strip().splitlines()
    return out.returncode == 0, tail[-1] if tail else ""


def _restart() -> None:
    subprocess.run(["systemctl", "--user", "restart", SERVICE], capture_output=True, timeout=120, check=False)


def _healthy(port: int) -> Callable[[], bool]:
    def check() -> bool:
        deadline = time.monotonic() + HEALTHY_WITHIN
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=3) as r:  # noqa: S310
                    if r.status == 200:
                        return True
            except OSError:
                pass
            time.sleep(1)
        return False

    return check


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m claude_voice.updater")
    parser.add_argument("--repo", required=True, type=Path)
    parser.add_argument("--log", default=str(LOG))
    parser.add_argument("--port", type=int, default=8811)
    args = parser.parse_args()
    outcome = run(
        args.repo,
        Path(args.log).expanduser(),
        install=lambda repo: _uv(repo, "sync", "--locked", "--all-extras"),
        tests=lambda repo: _uv(repo, "run", "pytest", "-q", "-p", "no:cacheprovider"),
        restart=_restart,
        healthy=_healthy(args.port),
    )
    return 0 if outcome["result"] in ("updated", "up_to_date") else 1


if __name__ == "__main__":
    sys.exit(main())


# The kinds of change a commit subject names (Conventional Commits), in the order they are told.
KINDS = (
    ("feat", "New"),
    ("fix", "Fixed"),
    ("perf", "Faster"),
    ("refactor", "Reworked"),
    ("docs", "Documentation"),
    ("test", "Tests"),
    ("build", "Build"),
    ("ci", "Continuous integration"),
    ("chore", "Housekeeping"),
    ("style", "Style"),
    ("revert", "Reverted"),
)
SUBJECT = re.compile(r"(?P<type>[a-z]+)(?:\([^)]*\))?(?P<bang>!)?:\s*(?P<rest>.+)")


def group_commits(commits: list[tuple[str, str]]) -> list[dict[str, Any]]:
    """Commits (short hash, subject), oldest first, grouped by kind with new things first."""
    known = dict(KINDS)
    groups: dict[str, list[dict[str, Any]]] = {}
    for sha, subject in commits:
        m = SUBJECT.fullmatch(subject.strip())
        kind = m["type"] if m and m["type"] in known else "other"
        text = m["rest"] if m and kind != "other" else subject.strip()
        groups.setdefault(kind, []).append({"sha": sha, "subject": text, "breaking": bool(m and m["bang"])})
    order = [k for k, _ in KINDS] + ["other"]
    titles = {**known, "other": "Other"}
    return [{"type": k, "title": titles[k], "commits": groups[k]} for k in order if k in groups]


def changes(repo: Path) -> dict[str, Any]:
    """What an update would bring: the commits on GitHub this clone has not pulled, grouped."""
    fetched, detail = _git(repo, "fetch", "--quiet")
    _, current = _git(repo, "rev-parse", "--short", "HEAD")
    _, latest = _git(repo, "rev-parse", "--short", "@{upstream}")
    ok, out = _git(repo, "log", "--reverse", "--format=%h%x00%s", "HEAD..@{upstream}")
    commits = [tuple(line.split("\0", 1)) for line in out.splitlines() if "\0" in line] if ok else []
    return {
        "current": current,
        "latest": latest,
        "behind": len(commits),
        "groups": group_commits([(sha, subject) for sha, subject in commits]),
        "fetched": fetched,
        **({} if fetched else {"note": f"Could not reach GitHub, so this may be old: {detail[-200:]}"}),
        "checked_at": _now(),
    }
