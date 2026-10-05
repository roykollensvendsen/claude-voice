# Decision records

A decision that costs more to reverse than to take is written down here before
the code that rests on it. A decision that only implements one already taken
is not; it is a commit message.

## The form

Every record is `ADR-CV-NNN-a-short-title.md`, with these sections in
this order:

| Section | Holds |
|---|---|
| Status | Accepted or Proposed, who decided, when; an objection returns it to Proposed |
| Context | what was true when the question arose, with sources where the facts are checkable |
| Options considered | every option weighed, including doing nothing, each with why it was not taken |
| Decision | what was chosen, in one paragraph |
| Consequences | what this costs, what it forecloses, and at least one thing that gets worse |
| Related | the pages it shapes |

Numbers are one sequence and never reused. A record is superseded by a later
one, never edited into a different decision.

Record the process choices too, not only the artefact ones. How a change is
made, and why, is what costs time on every future contribution, and it is the
decision nobody writes down.

## The records

| Id | Decides | Status |
|---|---|---|
| [ADR-CV-001](ADR-CV-001-a-bridge-over-the-agent-sdk.md) | A bridge over the Claude Agent SDK, on the user's subscription | Accepted |
| [ADR-CV-002](ADR-CV-002-oauth-in-the-bridge.md) | The bridge is its own OAuth server, with one owner | Accepted |
| [ADR-CV-003](ADR-CV-003-risky-actions-wait-for-a-spoken-yes.md) | Risky actions wait for a spoken yes | Accepted |
| [ADR-CV-004](ADR-CV-004-reach-open-sessions-by-messaging-not-takeover.md) | Reach sessions open elsewhere by messaging, never by taking them over | Accepted |
| [ADR-CV-005](ADR-CV-005-how-a-change-is-made.md) | How a change is made: tests first, one pull request per change, landed by rebase | Accepted |

What is deliberately left undone, and what would make each worth doing, is
[`deferred.md`](deferred.md). A thing left undone with no trigger is a thing
forgotten.
