# ADR-CV-015: The red run is proved by continuous integration, not by a red commit

## Status

Accepted by the owner, 2026-11-14. Supersedes the first decision
point and the first consequence of
[ADR-CV-005](ADR-CV-005-how-a-change-is-made.md).

## Context

ADR-CV-005 put the red run in the history: a `test:` commit, red on its own,
before the code. That only shows the order commits were made in, which can be
rearranged. It also leaves red commits on main for anyone running `git bisect`.

## Options considered

- **Keep red commits, and teach bisect to skip them.** The burden then falls on
  every bisect.
- **One commit, with the red run in the pull request body.** It's text, and it
  proves nothing.
- **One commit, with continuous integration running the pull request's tests
  against the code before it.** Chosen.

## Decision

- A change lands as commits that are each green, with a test and its code
  together.
- The `red-first` check runs the pull request's new and changed tests against
  its merge base, with only the tests applied. Each must fail there and pass on
  the branch. A function or class a test file already had, other than a test,
  keeps its old body there, since a fix may live in the helper itself. Its
  constants and data come from the branch, since a new test may read them.
- No new or changed tests means it passes.
- A test that pins behaviour that already exists is excused by a `Red-first:`
  line in the pull request body.

## Consequences

- Every commit on main is green, so bisect works.
- The proof is a run of the checks, not an order of commits.
- Worse: the red run is no longer visible in `git log`, only in the pull
  request's checks.
- Worse: a test made to fail in a way the check can't see, such as only on
  Windows, needs the marker.
- Worse: only test functions are counted. A change to shared test setup, such
  as a fixture in `conftest.py` or a helper, can change what existing tests
  check, and the check does not see it. The reviewer has to.
- Old red commits on main stay. History is not rewritten.

## Related

CONTRIBUTING.md ("How a change is made", step 3), `scripts/red_first.py`,
`.github/workflows/red-first.yml`, `tests/test_red_first.py`.
