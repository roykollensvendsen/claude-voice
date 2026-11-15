"""Follow the README's five-minute start on an empty machine, and check where it ends.

The block the README marks for "the clean-install job" is run as written in a
fresh Ubuntu container, except that the bridge is installed from this checkout
rather than from GitHub, so a pull request tests its own code. A stranger at
that point has every piece installed and has not yet signed in to Claude, so
`claude-voice check --json` must pass every check except the sign-in, and the
sign-in must say how to do it.

Then `claude-voice setup --check --json` must report exactly the steps an empty
machine still lacks, and exactly the ones only the person can take. The home
folder is listed, with each file's mode and hash, before and after it: the check
is read-only, so the two lists must match. Needs Docker. Usage: python3 scripts/clean_install.py
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
IMAGE = "buildpack-deps:noble-scm"  # Ubuntu 24.04 with curl, certificates and git already in it
SOURCE = "git+https://github.com/roykollensvendsen/claude-voice"
# What any desktop already has; the README assumes these and nothing more.
BASE = "command -v curl git >/dev/null"  # no package download, so a package-server outage cannot fail the check
#: What `setup --check` must still find missing after the README's five minutes: no Node.js,
#: no settings file, no service, no built companion app, and none of the profile's clones.
EXPECTED_MISSING = ["node", "config", "service", "ui_build", "repo:claude-voice", "repo:claude-voice-ui"]
#: Missing too, and optional, so they do not count against `ready`.
EXPECTED_OPTIONAL = ["repo:claude-voice-mobile"]
#: What only the stranger can do: sign in to Claude, and give the bridge a public address.
EXPECTED_MANUAL = ["signed_in", "phone_route"]
#: Lists every file under the home with its mode and hash, skipping what the README's own
#: installers keep there, so the list shows only what a check could have written.
LISTING = "find ~ -path ~/.cache -prune -o -type f -printf '%m %p ' -exec sha256sum {} \\; | sort -k2"


def steps() -> str:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    found = re.findall(r"<!-- not run:[^>]*the clean-install job runs it[^>]*-->\n```bash\n(.*?)```", readme, re.S)
    if len(found) != 1:
        sys.exit("clean_install: expected exactly one marked block in README.md")
    return found[0].replace(SOURCE, "/src")


def _section(out: str, name: str) -> str:
    """The text the container printed between `==name==` and the next marker."""
    match = re.search(rf"^=={name}==\n(.*?)(?=^==\w+==$|\Z)", out, re.S | re.M)
    return match.group(1) if match else ""


def check_the_check(report: dict[str, Any]) -> None:
    by_name = {c["name"]: c for c in report["checks"]}
    wrong = [c["name"] for c in report["checks"] if c["required"] and not c["ok"] and c["name"] != "signed_in"]
    if wrong:
        sys.exit(f"clean_install: after the README's steps these still fail: {', '.join(wrong)}")
    login = by_name.get("signed_in", {})
    if login.get("ok") or "/login" not in login.get("fix", ""):
        sys.exit(f"clean_install: the sign-in check should fail and say how to sign in: {login}")


def check_the_setup(report: dict[str, Any]) -> None:
    """The steps an empty machine lacks, exactly: one more or one fewer is a change to look at."""
    by_status: dict[str, list[str]] = {"missing": [], "optional": [], "manual": []}
    for step in report["steps"]:
        if step["status"] == "missing":
            by_status["optional" if step["optional"] else "missing"].append(step["name"])
        elif step["status"] == "manual":
            by_status["manual"].append(step["name"])
    expected = {"missing": EXPECTED_MISSING, "optional": EXPECTED_OPTIONAL, "manual": EXPECTED_MANUAL}
    for status, names in expected.items():
        if by_status[status] != names:
            sys.exit(
                f"clean_install: setup --check should report {status} {names}, and reports {by_status[status]}. "
                "If the README's steps changed on purpose, change EXPECTED_ in scripts/clean_install.py with them."
            )
    if report["ready"] or report["missing"] != EXPECTED_MISSING:
        sys.exit(f"clean_install: setup --check should not be ready, missing {EXPECTED_MISSING}: {report}")


def main() -> int:
    script = "\n".join(
        [
            BASE,
            "useradd -m stranger",
            "cp -r /src-ro /src && chown -R stranger /src",
            "su - stranger -c 'bash -s' <<'STEPS'",
            "set -x",
            steps(),
            "claude-voice check --json > /tmp/check.json",
            "command -v claude >/dev/null || echo 'claude-code-missing' >> /tmp/check.json",
            f"{LISTING} > /tmp/before.txt",
            "export PYTHONDONTWRITEBYTECODE=1  # Python's own cache of compiled modules is not the check's write",
            "claude-voice setup --check --json > /tmp/setup.json",
            "claude-voice setup --check > /dev/null",
            "claude-voice repos sync --check > /dev/null",
            f"{LISTING} > /tmp/after.txt",
            "STEPS",
            "echo '==check=='; cat /tmp/check.json",
            "echo '==setup=='; cat /tmp/setup.json",
            "echo '==changed=='; diff /tmp/before.txt /tmp/after.txt",
            "echo '==end=='",
        ]
    )
    run = subprocess.run(
        ["docker", "run", "--rm", "-i", "-v", f"{ROOT}:/src-ro:ro", IMAGE, "bash", "-c", script],
        capture_output=True,
        text=True,
        check=False,
    )
    sys.stderr.write(run.stderr[-4000:])
    if "claude-code-missing" in run.stdout:
        sys.exit("clean_install: Claude Code is not on the PATH after the README's steps")
    try:
        check = json.loads(_section(run.stdout, "check"))
        setup = json.loads(_section(run.stdout, "setup"))
    except ValueError:
        sys.stderr.write(run.stdout[-2000:])
        sys.exit("clean_install: the steps did not get as far as `claude-voice setup --check --json`")
    check_the_check(check)
    check_the_setup(setup)
    changed = _section(run.stdout, "changed").strip()
    if changed:
        sys.exit(f"clean_install: setup --check and repos sync --check changed files in the home:\n{changed}")
    print("clean_install: the README's steps work on an empty machine, up to signing in")
    print("clean_install: setup --check reports exactly the steps an empty machine lacks, and changes nothing")
    return 0


if __name__ == "__main__":
    sys.exit(main())
