# ADR-CV-021: The test suite has no time cap

## Status

Proposed 2026-11-22, under the owner's standing rule that time caps are
removed, never raised. Accepted when the owner merges it. It supersedes one line of
[ADR-CV-009](ADR-CV-009-main-keeps-the-quality-levels.md): "`quality.json`
keeps only the time budget".

## Context

The quality ratchet failed a change when the suite used more CPU time than a
budget in `quality.json`, 180 seconds plus a quarter. The CPU time was meant to
leave out a busy machine's waiting. It did not: on 2026-11-22 a loaded laptop
measured 247 seconds for a tree whose suite passed its budget in continuous
integration. A builder then had to
tell noise from a real slowdown by hand, and a budget is the kind of cap the
owner asked to have removed.

## Options considered

- **Do nothing.** Every busy afternoon brings red checks that mean nothing.
- **Raise the budget.** It moves the cliff, and the owner's rule is to remove
  caps, not raise them.
- **Compare with main's own time, as a level.** The same noise makes it fail
  just as often, in either direction.
- **Remove the cap, and show the time.** Nothing fails on time; the number
  stays in sight.

## Decision

The ratchet no longer judges how long the suite takes. It prints the seconds on
the clock and in CPU work, and fails nothing on them. `quality.json` is gone.
The health page still shows the time, and reads the file's history from git so
its older trend stays. A repository that still sets a budget in its own
`quality.json` is judged against it on the page, as before.

## Consequences

- A change can no longer go red because the machine was busy.
- Worse: a change that makes the suite slower passes. Only a reader of the
  ratchet's note or the health page sees it.
- The code-quality skill's advice on slow tests stays, as a fix worth making,
  not a gate.

## Related

- [ADR-CV-009](ADR-CV-009-main-keeps-the-quality-levels.md)
- [CONTRIBUTING.md](../CONTRIBUTING.md), the ratchet's measures
- `scripts/ratchet.py`, `src/claude_voice/software.py`
