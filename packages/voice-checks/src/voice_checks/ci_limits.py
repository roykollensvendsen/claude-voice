"""No CI job that runs tests or mutation carries a time limit.

The owner's rule is that time caps are removed, never raised. A limit on a test job cuts a
slow but honest run short, and the red it leaves says nothing about the change. A job that
runs no tests, such as one that lints commit messages, may keep its limit, but only when the
repository names it as such. Guessing from a job's lines would miss a new way of running
tests (#529). A named job whose lines still show tests is refused all the same.

Workflows are read as text, not as YAML, so the check needs nothing installed. A job is the
block under `jobs:` that starts at the first key's indent. Comment lines are skipped, so a
comment neither runs tests nor sets a limit.
"""

from __future__ import annotations

import pathlib
import re

#: What in a job's lines says it runs tests: pytest, a mutation run, or the quality ratchet.
# RULE: a mutation or ratchet job counts as running tests
RUNS_TESTS = re.compile(r"\bpytest\b|\bmutat\w*|\bratchet\b")
WRAPPERS = re.compile(
    r"\bmake\s+(?:test|check)\b|\btox\b|\bnox\b|\bhatch\s+test\b|\bjust\s+test\b"
    r"|\bgradlew\s+\S*[tT]est|\bnpm\s+(?:run\s+)?test\b|scripts/\S+\.sh\b"
)
# RULE: a time limit on a step counts too
LIMIT = re.compile(r"^\s*(?:-\s+)?timeout-minutes\s*:")
# RULE: a quoted job key is still a job
KEY = re.compile(r"""^( +)(["']?)([\w-]+)\2:\s*(?:#.*)?$""")
# RULE: a commented or quoted jobs key is still read
JOBS = re.compile(r"""^(["']?)jobs\1:\s*(?:#.*)?$""")


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _code(line: str) -> bool:
    """Whether a line says something: not blank, and not a comment."""
    return bool(line.strip()) and not line.lstrip().startswith("#")


def jobs(text: str) -> list[tuple[str, list[tuple[int, str]]]]:
    """Each job's name with its lines and their numbers, comments left out."""
    lines = text.splitlines()
    start = next((i for i, line in enumerate(lines) if JOBS.match(line)), None)
    if start is None:
        return []
    found: list[tuple[str, list[tuple[int, str]]]] = []
    depth = None
    for number, line in enumerate(lines[start + 1 :], start + 2):
        if not _code(line):
            continue
        if _indent(line) == 0:
            break
        depth = _indent(line) if depth is None else depth
        key = KEY.match(line)
        if key and len(key.group(1)) == depth:
            found.append((key.group(3), []))
        elif found:
            found[-1][1].append((number, line))
    return found


def scan(root: pathlib.Path, no_tests: frozenset[str] = frozenset()) -> list[str]:
    """Each time limit not allowed, as `path:line: what`.

    A limit is allowed only on a job named in `no_tests` whose lines show no tests.
    """
    folder = root / ".github" / "workflows"
    if not folder.is_dir():
        return []
    found = []
    for path in sorted([*folder.glob("*.yml"), *folder.glob("*.yaml")]):
        shown = path.relative_to(root).as_posix()
        text = path.read_text(encoding="utf-8")
        read = jobs(text)
        # RULE: a time limit in a file whose jobs cannot be read fails
        if not read and any(_code(line) and "timeout-minutes" in line for line in text.splitlines()):
            found.append(f"{shown}: has timeout-minutes, but its jobs could not be read")
        for name, lines in read:
            # RULE: a job that runs tests through a wrapper counts as running tests
            tests = any(RUNS_TESTS.search(line) or WRAPPERS.search(line) for _, line in lines)
            for number, line in lines:
                if not LIMIT.match(line):
                    continue
                if tests:
                    # RULE: a job that runs tests with a time limit is refused
                    found.append(f"{shown}:{number}: the {name} job runs tests and has timeout-minutes")
                # RULE: a time limit on a job not named as running no tests is refused
                elif name not in no_tests:
                    found.append(
                        f"{shown}:{number}: the {name} job has timeout-minutes"
                        " but is not named as running no tests (--no-tests)"
                    )
    return found
