# ADR-CV-009: Main keeps the quality levels, so a pull request never records them

## Status

Accepted 2026-11-10 by Roy Kollen Svendsen, in the terminal, choosing this over
two other options.

## Context

The quality ratchet stops the code getting worse: test coverage, the number of
tests, the mutation check, lint and type errors, import cycles and layers,
copied code, and how tangled each module's worst function is. Until now the
levels lived in `quality.json`, and every pull request had to record the exact
levels it reached, or the check failed.

That made the file a queue. On 2026-11-10 four open pull requests, two of them
from an outside contributor, failed for one reason: another pull request had
merged first, so their recorded number of tests was stale. Each had to be
rebased and measured again, about ten minutes a time, and contributors whose
machines measure differently, or cannot run the mutation check at all, had to
ask for it to be done for them.

What holds:

- The useful part of the check is that nothing gets worse than main. Recording
  the exact level in the pull request only exists so the next comparison has a
  floor.
- Continuous integration already measures every level on every pull request
  and on every push to main.
- A pull request's check runs on the pull request merged into main, so it is
  measured against the main it would land on.

## Options considered

- **Keep the file as it was.** Every merge breaks every other open pull
  request. Not taken.
- **Measure main and the pull request in every check.** Simple, with nothing
  stored, but every check takes twice as long, because the mutation check is
  slow. Not taken, except as the fallback below.
- **Keep the file but drop the exact recording**, tightening it now and then
  by hand. The smallest change, but the file would often show worse levels
  than the code has. Not taken.
- **Main keeps its measured levels with each of its runs; a pull request
  compares with them.** **Chosen.**

## Decision

- **After every push to main**, continuous integration measures the levels, as
  it already does, and keeps them with that run as `quality-levels`.
- **A pull request's check** takes the levels main measured for the commit it
  is merged onto, and fails only when a level is worse, unless a
  `Ratchet-Exception:` line in the pull request says why, as before.
- **When those levels are missing**, for a run that was cancelled or has expired,
  the check measures that commit itself in a copy of it. It is slower, and it
  never skips.
- **`quality.json` keeps only the time budget**, which is a limit set by hand
  rather than a level. No pull request needs to change it to pass.
- The floor still never slips back: main's levels are measured after every
  merge, and every merge passed the check against the main before it.

## Consequences

- Merging one pull request no longer breaks the others, and a contributor never
  runs the ratchet to fix a number.
- **Worse:** the levels no longer show in each pull request's diff. They are in
  the checks' output and in main's runs.
- **Worse:** the check depends on GitHub keeping main's run results, 90 days by
  default. Past that, or when a run is missing, it measures, which takes about
  ten minutes more.
- **Worse:** a pull request whose check ran before main moved on is not
  checked again until it changes, as before.
- The phone page's software panel reads its values and trend from main's kept
  levels too, downloading each run's once, and from `quality.json`'s history for
  the time before.

## Related

ADR-CV-005 (how a change is made), `scripts/ratchet.py`,
`.github/workflows/checks.yml`, and "The quality ratchet" in `CONTRIBUTING.md`.
