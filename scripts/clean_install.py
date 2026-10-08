"""Follow the README's five-minute start on an empty machine, and check where it ends.

The block the README marks for "the clean-install job" is run as written in a
fresh Ubuntu container, except that the bridge is installed from this checkout
rather than from GitHub, so a pull request tests its own code. A stranger at
that point has every piece installed and has not yet signed in to Claude, so
`claude-voice check --json` must pass every check except the sign-in, and the
sign-in must say how to do it. Needs Docker. Usage: python3 scripts/clean_install.py
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
IMAGE = "ubuntu:24.04"
SOURCE = "git+https://github.com/roykollensvendsen/claude-voice"
# What any desktop already has; the README assumes these and nothing more.
BASE = "apt-get update -qq && apt-get install -y -qq curl ca-certificates git >/dev/null"


def steps() -> str:
    readme = (ROOT / "README.md").read_text()
    found = re.findall(r"<!-- not run:[^>]*the clean-install job runs it[^>]*-->\n```bash\n(.*?)```", readme, re.S)
    if len(found) != 1:
        sys.exit("clean_install: expected exactly one marked block in README.md")
    return found[0].replace(SOURCE, "/src")


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
            "STEPS",
            "cat /tmp/check.json",
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
        report = json.loads(run.stdout[run.stdout.index("{") :])
    except ValueError:
        sys.stderr.write(run.stdout[-2000:])
        sys.exit("clean_install: the steps did not get as far as `claude-voice check --json`")
    by_name = {c["name"]: c for c in report["checks"]}
    wrong = [c["name"] for c in report["checks"] if c["required"] and not c["ok"] and c["name"] != "signed_in"]
    if wrong:
        sys.exit(f"clean_install: after the README's steps these still fail: {', '.join(wrong)}")
    login = by_name.get("signed_in", {})
    if login.get("ok") or "/login" not in login.get("fix", ""):
        sys.exit(f"clean_install: the sign-in check should fail and say how to sign in: {login}")
    print("clean_install: the README's steps work on an empty machine, up to signing in")
    return 0


if __name__ == "__main__":
    sys.exit(main())
