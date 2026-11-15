# ADR-CV-018: A paired machine powers off only on a yes the hub signed

## Status

Accepted by the owner, 2026-11-15, on a design that quality and voice-architect both
approved.

## Context

The owner wants to shut down or restart a paired machine by voice, the way the garage door
is opened: a question first, and an action only on his spoken yes. No Claude session may
ever decide this. A session on a paired machine refused a shutdown that arrived as a
message, and that refusal was right.

The agent on a paired machine runs as the machine user, and so do the Claude sessions it
starts. polkit cannot tell two processes of one user apart. A rule that lets the agent
power the machine off therefore lets every session of that user do it, with no password
and no token.

## Options considered

- **Grant the agent's user the power right, and tell sessions not to use it.** Cheap, but
  any session that tries can shut the machine down. A warning sign, not a lock.
- **Move the whole agent under its own system user.** It locks sessions out, but it is a
  large change to every paired machine, bigger than the feature.
- **A small helper of its own, which acts only on a yes signed by the hub.** Chosen.

## Decision

A separate helper, `claude-voice-power`, is the only subject polkit grants power to. It
listens on one Unix socket and acts only on a signature made by the hub's private key,
checked against a public key that only root can write. The hub signs inside the voice's
call and for nothing else. The agent only relays, and only after its machine user turned
the power tools on by typing the machine's name.

The helper never runs from anything a session can write. It is built as a wheel on a
reviewed release tag, installed by a root step under `/usr/local/lib`, and refuses to
start when any import path lies in a home folder or is writable by anyone but root. The
release's install script pins the wheel's hash and a hash-locked dependency set.

Nothing is installed on a machine until its user runs that script and types its name.

## Consequences

- A session on a paired machine can reach the socket and read the public key, and can do
  nothing but cancel. What stays open is root on that machine, and the hub's own sessions,
  which run as the owner and are kept from the signing key only by permission rules.
- A new package, a release workflow, a tag rule on GitHub and an install script are now
  part of the project. Each needs care at every release.
- Updating the helper is a separate root step, so the agent and the helper can run
  different versions for a while.
- The desktop notice a person at the machine would see is limited to `wall`, because the
  helper runs as another user and cannot reach a user's own message bus.

## Related

- [ADR-CV-003](ADR-CV-003-risky-actions-wait-for-a-spoken-yes.md), whose spoken yes this
  extends to a machine's power
- [ADR-CV-006](ADR-CV-006-other-machines-through-the-laptop.md), whose agent relays the
  tools
