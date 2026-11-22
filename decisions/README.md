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
| [ADR-CV-006](ADR-CV-006-other-machines-through-the-laptop.md) | Reach other machines through the laptop, with a small agent on each | Accepted |
| [ADR-CV-007](ADR-CV-007-a-phone-agent-and-voice-calls.md) | A phone joins the hub as an agent, under privacy rules every machine keeps | Proposed |
| [ADR-CV-008](ADR-CV-008-approvals-reach-the-phone-when-the-app-is-closed.md) | Approvals reach the phone when the app is closed | Accepted |
| [ADR-CV-009](ADR-CV-009-main-keeps-the-quality-levels.md) | Main keeps the quality levels, so a pull request never records them | Accepted |
| [ADR-CV-010](ADR-CV-010-a-known-plugin-is-approved-again-by-voice.md) | A plugin already approved is updated without typing, or approved again by voice | Accepted |
| [ADR-CV-011](ADR-CV-011-a-short-list-of-owner-actions-is-approved-by-voice.md) | A short, fixed list of the owner's own actions is approved by voice | Accepted |
| [ADR-CV-012](ADR-CV-012-a-session-opens-where-the-owner-is.md) | A session opens where the owner is | Accepted |
| [ADR-CV-013](ADR-CV-013-an-agreed-exception-is-shown-as-exempt.md) | A measure left outside its limit by agreement is shown as exempt, never green | Accepted |
| [ADR-CV-014](ADR-CV-014-each-programme-describes-itself-in-its-own-repository.md) | Each programme describes its org chart in a file in its own repository | Accepted |
| [ADR-CV-015](ADR-CV-015-the-red-run-is-proved-by-ci.md) | The red run is proved by continuous integration, not by a red commit | Accepted |
| [ADR-CV-016](ADR-CV-016-a-plugin-is-installed-from-its-own-manifest.md) | A plugin is installed from a data-only manifest it ships, in the owner's own terminal | Accepted |
| [ADR-CV-017](ADR-CV-017-a-profile-is-an-allow-listed-shape-without-values.md) | A profile is an allow-listed shape, and the values stay with the user | Accepted |
| [ADR-CV-018](ADR-CV-018-a-paired-machine-powers-off-only-on-a-yes-the-hub-signed.md) | A paired machine powers off only on a yes the hub signed, through a helper of its own | Accepted |
| [ADR-CV-019](ADR-CV-019-sessions-live-in-tmux-and-a-private-file-lists-them.md) | Sessions live in tmux, and a private file lists them for recovery | Accepted |
| [ADR-CV-021](ADR-CV-021-the-test-suite-has-no-time-cap.md) | The test suite has no time cap: its time is shown, never judged | Proposed |

What is deliberately left undone, and what would make each worth doing, is
[`deferred.md`](deferred.md). A thing left undone with no trigger is a thing
forgotten.
