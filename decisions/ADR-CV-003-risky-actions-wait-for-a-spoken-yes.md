# ADR-CV-003: Risky actions wait for a spoken yes

## Status

Accepted by Roy Kollen Svendsen, 2026-10-05.

## Context

The owner uses this while driving and will not touch the screen. In a voice
session, a Gmail connector that needed a tap to approve each action stopped the
whole flow. Claude Code asks permission for actions its rules do not already
allow, through a callback the Agent SDK exposes.

## Options considered

- **Pre-approve everything (bypass permissions).** Anyone holding a token
  could then run any command. Refused.
- **Ask on the phone screen.** Impossible while driving.
- **Turn each permission request into a pending approval answered by voice.**
  Chosen.

## Decision

- Read-only tools pass without asking.
- Anything else becomes a pending approval with a short number that can be
  spoken.
- The assistant reads it out and calls approve or deny.
- An approval nobody answers is refused after ten minutes.

## Consequences

- Hands-free work is possible, and nothing risky runs unattended.
- Worse: the bridge cannot tell an approval the owner spoke from one the
  assistant invented. The tool descriptions forbid the latter. The owner's own
  Claude Code permission rules remain the real backstop.

## Related

`src/claude_voice/approvals.py`, README.md (trust boundary).
