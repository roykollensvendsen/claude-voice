# ADR-CV-023: The mutation run and the ratchet run once per batch

## Status

Proposed 2026-11-23, from the batch-merge design that both reviewers agreed.
Accepted when the owner merges it. It changes where
[ADR-CV-009](ADR-CV-009-main-keeps-the-quality-levels.md)'s check runs, not
what it compares.

## Context

The `quality` job holds the mutation run and the ratchet. It was the slowest
job, and it ran on every pull request and on every push to main. Nine merges in
one evening started nine pull request runs and nine main runs, each with its
own mutation run. The owner asked for fewer runs.

On 2026-11-23 nine approved pull requests were applied together on one branch,
and continuous integration ran once on the result. That proved the combination,
and main's tree after the merges matched it exactly.

## Options considered

- **Do nothing.** Every pull request keeps its own mutation run, and main runs
  one more per merge.
- **GitHub's merge queue.** Per GitHub's documentation, it is offered for
  repositories an organisation owns. This one is owned by a user.
- **Run `quality` once per batch.** Approved pull requests are applied together
  on an `integration/` branch, and `quality` runs there. A status called
  `batch` marks each member's head once that run passed.

## Decision

- An ordinary pull request leaves `quality` out. `scripts/ci_scope.py` decides
  it, so the gate counts the skip as a pass.
- A pull request from an `integration/` branch runs it. The name only ever adds
  the run, so claiming it costs nothing.
- A push to main runs it. Pushes to main share one group that stops older runs,
  so a batch's last merge is the one checked in full.
- Dependabot's own pull request still runs it, and a job that checks nothing
  out then sets `batch` on its head. Only that job may write statuses.
- The nightly run is unchanged.

## Consequences

- One mutation run per batch, rather than one per pull request and one per
  merge.
- Worse: a pull request merged outside a batch skips the mutation run. Main's
  protection requiring `batch` in place of `quality` closes that. It is the
  owner's settings change, made after this one merges.
- The commits between a batch's first and last merge are never checked on
  their own. Their combined tree is what the batch proved.
- A reviewer's stamp names the patch it read. When an earlier member changes the
  lines around a later one, the later patch applies differently, and the batch
  leaves it out. That fails safe. The fix is a later batch on the new main, not
  a new stamp on the old head.
- A main run stopped by a later push keeps no levels. The ratchet then measures
  the base itself, as it already does for a missing run.

## Related

- [ADR-CV-009](ADR-CV-009-main-keeps-the-quality-levels.md)
- [CONTRIBUTING.md](../CONTRIBUTING.md), the quality ratchet
- `.github/workflows/checks.yml`, `scripts/ci_scope.py`
