# ADR-CV-022: A yes the support session recorded is checked against the owner's own turn

## Status

Accepted 2026-11-21 by the owner, with the support role's design. The check
shipped in #441 on 2026-11-22. This record was asked for in #495.

## Context

The owner talks to one support session, typed or by voice, so the manager can
coordinate the work. Support records each decision of the owner's as one line,
with a pointer to the turn that holds the owner's words. The manager then acts
on that line.

A line on its own is only a claim. Any session can write a file, and any
session can send support a message. Until now a yes counted only when said
directly to the manager, and a relay from a peer never counted. That rule has
to hold when the yes is said to support instead.

## Options considered

- **Keep every yes with the manager.** The owner would still have to find the
  manager's window for each decision, which support was made to end.
- **Trust support's message.** A message from a session is the very relay the
  owner's rule refuses.
- **Check each recorded line against support's own transcript.** The manager
  reads the owner's words where they were said, before acting.

## Decision

The manager acts on a recorded yes only after `claude-voice decisions check`
accepts it. The check reads support's own transcript, and accepts the line
only when all of these hold:

- the turn is the owner's: typed in support's own window, or carried from the
  voice by the bridge's own courier;
- the quote is the whole of the owner's words in that turn;
- support's question just before it named the item, the action and the sha;
- the item is still at that sha, given in full;
- the line has not been accepted before.

Anything else is refused, and every check is logged for the owner to read
after. The details live in
[`src/claude_voice/decisions.py`](../src/claude_voice/decisions.py) and in the
README's section
[Check a yes the support role recorded](../README.md#check-a-yes-the-support-role-recorded).

## Consequences

- The owner can give a decision to support alone, typed or by voice.
- A yes that support recorded wrongly, or that the item has moved past, is
  refused, and the owner is asked again.
- Worse: every decision takes one more step, and the manager waits for the
  check before acting.
- The check trusts the bridge's own records of the sessions it started. It is
  as strong as those records.

## Related

- [ADR-CV-011](ADR-CV-011-a-short-list-of-owner-actions-is-approved-by-voice.md)
- #441, the check; #435, the stamp on each delivered turn
