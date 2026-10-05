# ADR-CV-005: How a change is made: tests first, one pull request per change, landed by rebase

## Status

Accepted by Roy Kollen Svendsen, 2026-10-05.

## Context

Most changes here were asked for by voice, often while driving, and carried out
by an agent. A request spoken in a car is easy to misread, and an agent can
produce plausible code that has never been shown to fail. The project began
without a repository, and its first history was lost when a phone call ended
the session that held it.

## Options considered

- **No process.** Fast, but nothing then shows that a feature does what was
  asked.
- **Review by the owner before each merge.** The owner is often driving.
- **Tests first, every change in its own commits, gates in CI, landed by
  rebase.** Chosen.

## Decision

- Every feature or fix begins as a test that fails, committed before the code
  that turns it green.
- A rule the code enforces carries a `# RULE:` marker and a test in its own
  words, and switching it off must turn that test red.
- Changes reach `main` through pull requests that land by rebase, so each
  commit stays in the history and has to stand alone.

## Consequences

- Each red-then-green pair in the history shows what was asked and that the
  answer was checked.
- Worse: a change costs at least two commits and a pull request, which is slow
  for a one-line fix, and a test-first commit is deliberately red on its own.

## Related

CONTRIBUTING.md, `scripts/mutations.toml`, `tests/test_rules.py`.
