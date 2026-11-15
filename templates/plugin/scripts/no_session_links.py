#!/usr/bin/env python3
"""Refuse a commit message that links to a Claude session, or carries a trailer it should not.

A Claude-Session trailer, or any address of a claude.ai/code session, points into one
person's account: to everyone else reading this public history it is a dead link that says
nothing. Credit stays: a commit's trailers may be `Co-Authored-By`, and an outside
contributor's `Signed-off-by`, which is never refused; nothing else. Any other note belongs
in the pull request's text, where a reviewer reads it.

    python scripts/no_session_links.py origin/main..HEAD
    python scripts/no_session_links.py --file .git/COMMIT_EDITMSG

Only the commits of a range are read. Continuous integration passes the range a pull
request or a push adds, so older history is never checked. The second form is what the
commit-msg hook uses. Exit 1 names every message that fails, and why.
"""

from __future__ import annotations

import pathlib
import re
import subprocess
import sys

SESSION = re.compile(r"claude-session\s*:|claude\.ai/code/session_", re.IGNORECASE)
#: A line shaped like a git trailer: a key of letters, digits and hyphens, a colon, a value.
TRAILER = re.compile(r"^([A-Za-z][A-Za-z0-9-]*):\s+\S")
#: The trailers a commit may carry, compared without regard to case.
ALLOWED_TRAILERS = frozenset({"co-authored-by", "signed-off-by"})
#: `--file PATH` takes exactly two arguments.
FILE_ARGS = 2


def trailers(message: str) -> list[str]:
    """The keys of the message's trailers: its last paragraph, when every line of it is one."""
    paragraphs = [p for p in re.split(r"\n\s*\n", message.strip()) if p.strip()]
    if len(paragraphs) < 2:
        return []  # the subject alone is never a trailer block
    lines = [line for line in paragraphs[-1].splitlines() if line.strip()]
    matches = [TRAILER.match(line) for line in lines]
    return [m.group(1) for m in matches if m] if all(matches) else []


def offence(message: str) -> str | None:
    """Why `message` is refused, or None."""
    # RULE: a commit message never links to a Claude session
    if SESSION.search(message):
        return "links to a Claude session; leave out the Claude-Session line and claude.ai/code/session addresses"
    unknown = [key for key in trailers(message) if key.lower() not in ALLOWED_TRAILERS]
    # RULE: a commit carries no trailer but Co-Authored-By and Signed-off-by
    if unknown:
        return (
            f"has the trailer {', '.join(unknown)}; a commit may carry Co-Authored-By and Signed-off-by"
            " only, so move the note into the pull request's text"
        )
    return None


def offenders(commit_range: str, cwd: str | None = None) -> list[tuple[str, str, str]]:
    """(hash, subject, why) of every commit in `commit_range` whose message is refused."""
    out = subprocess.run(
        ["git", "log", "--format=%H%x00%B%x01", commit_range],
        capture_output=True,
        text=True,
        check=True,
        cwd=cwd,
    ).stdout
    found = []
    for record in out.split("\x01"):
        if "\x00" not in record:
            continue
        sha, message = record.strip("\n").split("\x00", 1)
        why = offence(message)
        if why:
            found.append((sha, message.strip().splitlines()[0], why))
    return found


def main() -> int:
    """Check a range of commits, or one message file; exit 1 on any offence."""
    args = sys.argv[1:]
    if len(args) == FILE_ARGS and args[0] == "--file":
        with pathlib.Path(args[1]).open(encoding="utf-8") as f:
            message = "\n".join(line for line in f.read().splitlines() if not line.startswith("#"))
        reason = offence(message)
        if reason:
            sys.stderr.write(f"The commit message {reason}.\n")
            return 1
        return 0
    if len(args) != 1:
        sys.stderr.write(f"{__doc__}\n")
        return 2
    found = offenders(args[0])
    for sha, subject, why in found:
        sys.stderr.write(f"{sha[:12]} {subject}: {why}\n")
    if not found:
        sys.stdout.write(f"No commit in {args[0]} links to a Claude session or carries another trailer.\n")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
