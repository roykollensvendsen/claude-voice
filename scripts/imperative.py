#!/usr/bin/env python3
"""Refuse a commit subject that is not in the imperative.

`committed` has an `imperative_subject` setting, and it fires only on a
lowercase first word. The same configuration requires the subject to be
capitalised, so its rule can never fire; and even lowercase it misses
"bumped", "refactored", "wrote" and "made". The setting is kept true so the
intent is recorded, and this is what enforces it.

    python scripts/imperative.py origin/main..HEAD
    python scripts/imperative.py --all
    python scripts/imperative.py --file .git/COMMIT_EDITMSG

The second form is what the commit-msg hook uses, so a subject is refused
while it is being written rather than in CI afterwards.

The rule: the subject completes the sentence "if applied, this commit will
...". That test is Chris Beams' (2014), not git's; git's own SubmittingPatches
asks for the imperative without offering a test for it. So "Add the check",
never "Added", "Adds" or "Adding". Exit 1 names every subject that fails.
"""

from __future__ import annotations

import pathlib
import re
import subprocess
import sys

# Imperative verbs that happen to end the way a past tense, a plural or a
# gerund does. Without these the rule would refuse perfectly good subjects.
ALLOWED = {
    "address",
    "bless",
    "bring",
    "cross",
    "discuss",
    "dismiss",
    "embed",
    "exceed",
    "express",
    "feed",
    "focus",
    "guess",
    "miss",
    "need",
    "pass",
    "ping",
    "press",
    "proceed",
    "process",
    "read",
    "ring",
    "seed",
    "shed",
    "sing",
    "speed",
    "spread",
    "string",
    "succeed",
    "toss",
}

# Irregular past tenses, which no ending gives away.
PAST = {
    "began",
    "broke",
    "brought",
    "built",
    "came",
    "caught",
    "chose",
    "did",
    "drew",
    "fell",
    "found",
    "gave",
    "got",
    "grew",
    "held",
    "kept",
    "knew",
    "led",
    "left",
    "lost",
    "made",
    "met",
    "paid",
    "ran",
    "said",
    "saw",
    "sent",
    "showed",
    "sold",
    "spoke",
    "stood",
    "taught",
    "thought",
    "told",
    "took",
    "understood",
    "went",
    "won",
    "wrote",
}

# Words that open a noun phrase or a question, and never an instruction.
NOT_A_VERB = {
    "a",
    "an",
    "the",
    "this",
    "that",
    "these",
    "those",
    "it",
    "there",
    "what",
    "which",
    "why",
    "how",
    "when",
    "where",
    "who",
    "whose",
}

SUBJECT = re.compile(r"^(?:\w+(?:\([^)]*\))?!?:\s*)?(\w+)")


def offence(subject: str) -> str | None:
    """Say what is wrong with a subject's first word, or nothing."""
    match = SUBJECT.match(subject)
    if not match:
        return None
    word = match.group(1).lower()
    if word in ALLOWED:
        return None
    tests = (
        # RULE: a subject that opens a noun phrase is refused
        (word in NOT_A_VERB, "opens a phrase rather than an instruction"),
        (word in PAST, "is a past tense"),
        (word.endswith("ing"), "is a gerund"),
        # RULE: a subject in the past tense is refused
        (word.endswith("ed"), "is a past tense"),
        (word.endswith("s") and not word.endswith("ss"), "is third person, not imperative"),
    )
    for failed, why in tests:
        if failed:
            return f"'{match.group(1)}' {why}"
    return None


def subjects(commit_range: str, cwd: str | None = None) -> list[tuple[str, str]]:
    """Every commit in the range, as its short hash and its subject.

    ``cwd`` exists so a test can build its own history and read that, rather
    than reading whatever the checkout happens to be. It is never passed in
    normal use.
    """
    result = subprocess.run(  # noqa: S603
        ["git", "log", "--no-merges", "--format=%h\t%s", commit_range],  # noqa: S607
        capture_output=True,
        text=True,
        check=True,
        cwd=cwd,
    )
    return [(line.split("\t", 1)[0], line.split("\t", 1)[1]) for line in result.stdout.splitlines() if "\t" in line]


def from_file(path: str) -> list[tuple[str, str]]:
    """The subject of a message being written, as the commit-msg hook sees it."""
    for line in pathlib.Path(path).read_text().splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            return [(pathlib.Path(path).name, stripped)]
    return []


def main() -> int:
    """Check a message file, every commit, a range, or the last commit."""
    if len(sys.argv) > 2 and sys.argv[1] == "--file":  # noqa: PLR2004
        where = sys.argv[2]
        pairs = from_file(where)
    elif sys.argv[1:2] == ["--all"]:
        # Not root..HEAD: a range excludes its own start, so asking for the
        # whole history that way silently skips the first commit.
        where = "every commit"
        pairs = subjects("HEAD")
    else:
        where = sys.argv[1] if len(sys.argv) > 1 else "HEAD~1..HEAD"
        pairs = subjects(where)
    found = [(name, subject, wrong) for name, subject in pairs if (wrong := offence(subject))]
    for name, subject, wrong in found:
        print(f"{name}: {wrong}; a subject completes 'if applied, this commit will ...'")  # noqa: T201
        print(f"    {subject}")  # noqa: T201
    if not found:
        print(f"Every subject in {where} is in the imperative.")  # noqa: T201
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
