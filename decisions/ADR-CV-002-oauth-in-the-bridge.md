# ADR-CV-002: The bridge is its own OAuth server, with one owner

## Status

Accepted by Roy Kollen Svendsen, 2026-10-05.

## Context

Phone apps that connect to a custom MCP server sign in with OAuth: they
register themselves, send the user to a sign-in page, and receive tokens. A
fixed bearer header was the first design, but ChatGPT's plugin creator offered
only OAuth or no sign-in, and the Claude app's connectors behave the same way.

## Options considered

- **No sign-in.** Anyone who finds the address could run code on the computer.
  Refused.
- **A fixed bearer token only.** Phone apps cannot send one.
- **An external identity provider.** One more service and account to depend on,
  for a single user.
- **OAuth served by the bridge, with the MCP library supplying the protocol.**
  Chosen.

## Decision

- Any client may register itself.
- A client gets a code only after the owner types the login secret on the
  bridge's own sign-in page, with five tries per attempt.
- Access tokens last an hour. Refresh tokens last 90 days and are replaced on
  each use.
- Both are stored only as hashes.
- The same secret, sent as a bearer token, also opens the bridge to local tools.

## Consequences

- One secret to guard, kept in a file only the owner can read.
- Worse: signing in proves the caller is the owner's assistant, not that the
  owner said yes to a given action. That gap is ADR-CV-003's to narrow.

## Related

`src/claude_voice/oauth.py`, README.md (Signing in).
