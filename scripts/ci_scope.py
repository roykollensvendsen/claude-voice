"""Which of CI's slower jobs a pull request needs, and whether the jobs that ran add up to a pass.

    python3 scripts/ci_scope.py scope    # writes windows=, python313=, template= to GITHUB_OUTPUT
    python3 scripts/ci_scope.py gate     # reads NEEDS, the run's jobs and results, and exits 1 on a fail

A push to main and the nightly run need every job. A pull request needs Windows, Python 3.13
and the plugin template only when it changes a path that one of them is there to catch. The
lists below are where a new path is added, on purpose; a test checks each one still names a file.

`gate` is the one job a branch rule can require instead of the many. A job skipped because
the scope said so passes; one skipped for any other reason, cancelled or failed, does not.
It runs only the standard library, so it needs no install step before it.
"""

from __future__ import annotations

import fnmatch
import json
import os
import subprocess
import sys

# A change to how CI itself is run, or to the lock, may break any job, so it runs them all.
# The shared test helpers are in it too, since every test leans on them.
EVERYTHING = (
    ".github/workflows/*",
    "scripts/ci_scope.py",
    "pyproject.toml",
    "uv.lock",
    "tests/conftest.py",
    "tests/fakes*.py",
)

# The files where the bridge touches files, processes or terminals, which differ on Windows.
# A test fails when a module that does is neither here nor in WINDOWS_EXEMPT.
WINDOWS = (
    *EVERYTHING,
    *(
        f"src/claude_voice/{name}.py"
        for name in (
            "agent",
            "approver_pane",
            "cli",
            "config",
            "cpu_watch",
            "cv",
            "doctor",
            "gitstatus",
            "guard",
            "held_folder",
            "install*",
            "invocation",
            "metrics",
            "org",
            "permissions",
            "plugin_venv",
            "plugins",
            "presence",
            "procs",
            "renames",
            "reposync",
            "restarts",
            "routing",
            "safe_git",
            "safefile",
            "server",
            "session_hook",
            "shipped",
            "software",
            "stalled",
            "tailnet",
            "temp_disk",
            "terminals*",
            "transcripts",
            "updater",
            "work",
        )
    ),
    "deploy/*",
    "scripts/clean_install.py",
)

# A module that touches processes or files, kept off the Windows list on purpose, with why.
WINDOWS_EXEMPT: dict[str, str] = {}

# Only a change of supported versions or of the tools can break one version and not the other.
PYTHON313 = EVERYTHING

# The template is rendered and put through its own gates, by the mutation runner and the ratchet.
TEMPLATE = (
    *EVERYTHING,
    "templates/*",
    "scripts/mutate.py",
    "scripts/ratchet.py",
    "tests/test_plugin_template.py",
)

JOBS = {"windows": WINDOWS, "python313": PYTHON313, "template": TEMPLATE}


def scope(event: str, paths: list[str]) -> dict[str, bool]:
    """Which slower jobs this event needs: all of them, except on a pull request."""
    # RULE: only a pull request may leave out the slower jobs
    if event != "pull_request":
        return dict.fromkeys(JOBS, True)
    return {job: any(fnmatch.fnmatch(p, glob) for p in paths for glob in globs) for job, globs in JOBS.items()}


# Each job the scope can leave out, by the name it has in checks.yml.
LEFT_OUT = {"windows": "windows", "template": "template"}


def gate(needs: dict[str, dict], actor: str = "") -> list[str]:
    """What keeps the run from passing, one line a job; none when it passes."""
    said = needs.get("scope", {})
    if said.get("result") != "success":
        return [f"scope: {said.get('result', 'missing')}, so nothing can be left out"]
    outputs = said.get("outputs", {})
    skippable = {job for key, job in LEFT_OUT.items() if outputs.get(key) == "false"}
    if actor == "dependabot[bot]":
        skippable.add("commits")  # its messages are not a person's to fix; the job skips itself
    wrong = []
    for job, ran in sorted(needs.items()):
        result = ran.get("result")
        # RULE: the gate passes a skipped job only when the scope left it out
        if result == "success" or (result == "skipped" and job in skippable):
            continue
        wrong.append(f"{job}: {result}")
    return wrong


def changed(base: str, head: str) -> list[str]:
    """Every path the pull request changed. A move lists both its old and its new path."""
    # RULE: a moved file counts under its old path as well as its new one
    diff = ["git", "diff", "--name-only", "--no-renames", f"{base}...{head}"]
    out = subprocess.run(diff, check=True, capture_output=True, text=True)
    return out.stdout.split()


def main(argv: list[str]) -> int:
    if argv[1:] == ["scope"]:
        event = os.environ.get("EVENT", "")
        paths = changed(os.environ["BASE"], os.environ["HEAD"]) if event == "pull_request" else []
        found = scope(event, paths)
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as out:
            for job, needed in found.items():
                out.write(f"{job}={'true' if needed else 'false'}\n")
                print(f"{job}: {'runs' if needed else 'left out, no path of its own changed'}")
        return 0
    if argv[1:] == ["gate"]:
        wrong = gate(json.loads(os.environ["NEEDS"]), os.environ.get("ACTOR", ""))
        for line in wrong:
            print(line, file=sys.stderr)
        print("gate: fail" if wrong else "gate: pass")
        return 1 if wrong else 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
