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
- all the test run printed, or printed before its time ran out, is kept in update-test.log
  beside the log, readable by the owner only, and the log's own test step names the tests
  that failed;
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
import unicodedata
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import cpu_watch, procs, redact, restarts, safe_git, safefile
from .config import STATE_DIR

LOG = Path("~/.local/state/claude-voice/update.json")
SERVICE = "claude-voice"
HEALTHY_WITHIN = 60.0
#: Everything the latest update's test run printed, next to the update's own log.
TEST_LOG = "update-test.log"
#: The test step's detail is kept to this many characters, room for every failing test it names;
#: every other step keeps its last 400.
TEST_DETAIL = 2000


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


# The bridge runs as a systemd user service, which cannot reach the owner's ssh-agent. So the
# updater's own git commands read GitHub's ssh addresses over HTTPS: a public repository needs
# no key at all. Only these commands; the owner's remotes and terminals keep using ssh.
OVER_HTTPS = ("-c", "url.https://github.com/.insteadOf=git@github.com:")
#: Every other way of reaching a remote is switched off, so no remote the clone's own settings
#: name can make the updater start a program (ssh, or a remote helper). A local clone stays allowed.
HTTPS_ONLY = ("-c", "protocol.allow=never", "-c", "protocol.https.allow=always", "-c", "protocol.file.allow=always")


def _git(repo: Path, *args: str) -> tuple[bool, str]:
    git = procs.find("git") or "git"
    try:
        # RULE: the updater reaches github over https not the owners ssh key
        leading = (*OVER_HTTPS, *HTTPS_ONLY, "-C", str(repo))
        out = safe_git.run(*leading, *args, git=git, capture_output=True, text=True, timeout=120, check=False)
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

    def step(self, step: str, ok: bool, detail: str, keep: int = 400) -> None:
        """Add a step, keeping the last `keep` characters of its detail."""
        self.body["steps"].append({"step": step, "ok": ok, "detail": detail[-keep:], "at": _now()})
        self.write()

    def finish(self, result: str, detail: str) -> dict[str, Any]:
        self.body.update(running=False, result=result, detail=detail, finished_at=_now())
        self.write()
        return {"result": result, "detail": detail}


#: How each finished update is said, by its result.
ENDINGS = {
    "updated": "The update started at {at} worked. {detail}",
    "up_to_date": "The update started at {at} found nothing new. {detail}",
    "rolled_back": "The update started at {at} was rolled back at the {failed} step{problem}. {detail}",
    "stopped": "The update started at {at} stopped at the {failed} step. {detail}",
}


def _clock(at: object) -> str:
    """An ISO time as the hour and minute on this machine's clock."""
    try:
        return datetime.fromisoformat(str(at)).astimezone().strftime("%H:%M")
    except ValueError:
        return "an unknown time"


def _one_line(detail: str) -> str:
    """The first line of a step's detail that says something, short enough to read out."""
    return _spoken(next((line.strip() for line in detail.splitlines() if line.strip()), ""))


def _spoken(text: str) -> str:
    """Words from git, uv or the service manager as they may be said: secrets hidden, no
    control characters, at most 200 characters."""
    # RULE: a failed step's words are said with secrets hidden and control characters gone
    clean = "".join(" " if unicodedata.category(ch).startswith("C") else ch for ch in redact.mask(text))
    return redact.hide(" ".join(clean.split()), 200)


def status(body: dict[str, Any] | None, running: bool) -> dict[str, Any]:
    """What the latest update is doing, or how it ended, from its log: short enough to say."""
    if not body or not (body.get("started_at") or body.get("steps")):
        return {"state": "none", "say": "No update has run on this bridge yet."}
    steps = body.get("steps") or []
    at, last = _clock(body.get("started_at")), (steps[-1]["step"] if steps else "first")
    keep = ("started_at", "finished_at", "from", "to", "detail")
    out: dict[str, Any] = {key: body[key] for key in keep if body.get(key)} | {"step": last}
    if out.get("detail"):
        out["detail"] = _spoken(str(out["detail"]))
    if running:
        return out | {"state": "running", "say": f"An update has run since {at}; it is at the {last} step."}
    # RULE: an update whose process died is said to have stopped without finishing
    if body.get("running"):
        said = f"The update started at {at} stopped at the {last} step without finishing."
        return out | {"state": "interrupted", "say": said}
    return _ended(body, out, at, last)


def _ended(body: dict[str, Any], out: dict[str, Any], at: str, last: str) -> dict[str, Any]:
    """How a finished update ended, with the step that failed and why, if one did."""
    steps = body.get("steps") or []
    result = str(body.get("result") or "stopped")
    out |= {"state": result, "rolled_back": result == "rolled_back"}
    failed = next((s for s in steps if not s.get("ok")), None)
    # RULE: a rolled back update names the step that failed and why in one line
    if failed is not None:
        out |= {"failed_step": failed["step"], "problem": _one_line(str(failed.get("detail") or ""))}
    words = ENDINGS.get(result, "The update started at {at} ended. {detail}")
    problem = out.get("problem", "")
    # RULE: a failed step with nothing to say is named without an empty reason
    reason = f": {problem}" if problem else ""
    fill = {"at": at, "failed": out.get("failed_step", last), "problem": reason, "detail": out.get("detail", "")}
    return out | {"say": words.format(**fill).strip()}


def read(path: Path = LOG) -> dict[str, Any] | None:
    """The latest update's log, or None when there is none to read."""
    try:
        body = json.loads(path.expanduser().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return body if isinstance(body, dict) else None


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
    note: Callable[[str, str, str], object] = lambda reason, frm, to: None,
    rules: Callable[[str], str] = lambda sha: "",
) -> dict[str, Any]:
    """Update the clone, and say why before each restart through `note(reason, from, to)`."""
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
            # RULE: a rollback notes why before it restarts the bridge
            note("rollback", after, before)
            restart()
            back = healthy()
            log.step("rollback", ok and back, f"back on {before[:7]}; {'healthy' if back else 'NOT healthy'}")
        else:
            log.step("rollback", ok, f"back on {before[:7]}")
        return log.finish("rolled_back", reason)

    for step, action, keep in (("install", install, 400), ("test", tests, TEST_DETAIL)):
        ok, out = action(repo)
        log.step(step, ok, out, keep)
        # RULE: an update whose tests fail is rolled back without a restart
        if not ok:  # roll back
            return roll_back(f"The new version failed to {step}; the old one is kept.", restarted=False)

    log.step("restart", True, "the voice drops for a few seconds")
    # RULE: an update notes why before it restarts the bridge
    note("update", before, after)
    restart()
    if not healthy():
        log.step("verify", False, "the bridge did not answer after the restart")
        return roll_back("The new version did not start; the old one is back.", restarted=True)
    log.step("verify", True, "the bridge answers")
    # RULE: an update brings the shipped rules to the repositories once the bridge answers
    log.step("rules", True, rules(after))
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


#: How many failing tests the test step's detail names; the rest are counted.
NAMED = 8
FAILURE = re.compile(r"^(FAILED|ERROR) ")
SUMMARY = re.compile(r"\b\d+ (passed|failed|errors?|skipped|deselected)\b|\bno tests ran\b")


def failure_summary(output: str) -> str:
    """The failing tests pytest named, at most eight, then its last summary line."""
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    failures = [line[:200] for line in lines if FAILURE.match(line)]
    rest = [line for line in lines if not FAILURE.match(line)]
    summary = next((line for line in reversed(rest) if SUMMARY.search(line)), rest[-1] if rest else "")
    more = [f"and {len(failures) - NAMED} more"] if len(failures) > NAMED else []
    return "\n".join([*failures[:NAMED], *more, summary[:200]]).strip()


#: CPU seconds the update's test run may use: three times the suite's own budget, so only a spin reaches it.
TESTS_CPU_CAP = 540.0


def run_tests(repo: Path, keep: Path) -> tuple[bool, str]:
    """Run the suite, keep all it printed in `keep`, and name the tests that failed."""
    uv = procs.find("uv")
    if not uv:
        return False, "uv is not installed"
    command = [uv, "run", "pytest", "-q", "-p", "no:cacheprovider", "-rfE"]
    try:
        # stderr goes in with stdout, so a warning printed last cannot hide the summary.
        # A busy machine only slows the run: it is stopped on CPU, never on the clock.
        done = cpu_watch.run(command, repo, cap=TESTS_CPU_CAP)
    except (OSError, subprocess.SubprocessError) as exc:
        safefile.replace(keep, str(exc))
        return False, str(exc)
    said = f"{done.output.rstrip()}\n{done.note}\n".lstrip() if done.note else done.output
    safefile.replace(keep, said)
    return done.code == 0, failure_summary(said)


def _uv(repo: Path, *args: str) -> tuple[bool, str]:
    uv = procs.find("uv")
    if not uv:
        return False, "uv is not installed"
    try:
        # No CPU cap for an install: only a run that has hung is stopped.
        done = cpu_watch.run([uv, *args], repo, cap=None, what=f"uv {args[0]}" if args else "uv")
    except (OSError, subprocess.SubprocessError) as exc:
        return False, str(exc)
    tail = [*done.output.strip().splitlines(), *([done.note] if done.note else [])]
    return done.code == 0, tail[-1] if tail else ""


SHIPPED_SECONDS = 300.0


def shipped_rules(repo: Path) -> str:
    """Run the new version's `permissions --shipped` and say what it did, briefly. It runs as
    its own process, so the rules and the code that judges them are the new version's."""
    uv = procs.find("uv")
    if not uv:
        return "uv is not installed, so the shipped rules were not brought to the repositories"
    command = [uv, "run", "--locked", "claude-voice", "permissions", "--shipped"]
    try:
        run = subprocess.Popen(
            command,
            cwd=repo,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=sys.platform != "win32",
        )
    except OSError as exc:
        return f"the shipped rules were not brought to the repositories: {exc}"
    try:
        said, _ = run.communicate(timeout=SHIPPED_SECONDS)
    except subprocess.TimeoutExpired:
        # RULE: a shipped rules step that runs too long is stopped with everything it started
        if sys.platform == "win32":
            run.kill()
        else:
            import signal  # here, as SIGKILL exists only off Windows

            os.killpg(run.pid, signal.SIGKILL)
        try:
            run.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        return f"the shipped rules step ran past {SHIPPED_SECONDS:.0f} s and was stopped"
    return (said or "").strip()[-2000:]


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
        tests=lambda repo: run_tests(repo, Path(args.log).expanduser().parent / TEST_LOG),
        restart=_restart,
        healthy=_healthy(args.port),
        note=lambda reason, frm, to: restarts.note(STATE_DIR, reason, "update_bridge", frm=frm, to=to),
        rules=lambda sha: shipped_rules(args.repo),
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
