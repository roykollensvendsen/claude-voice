# ADR-CV-007: A phone joins the hub as an agent

## Status

Proposed 2026-11-08. Settled with Roy Kollen Svendsen by voice on 2026-11-08.
Nothing beyond the first prototype is built until he accepts this record.

This record holds what the hub depends on. How the phone app is built inside is
[ADR-CVM-003 in claude-voice-mobile](https://github.com/roykollensvendsen/claude-voice-mobile/blob/main/decisions/ADR-CVM-003-the-apps-internal-design.md).

## Context

ADR-CV-006 made every extra machine a small claude-voice agent. The hub (the
laptop running the bridge) dials each one over Tailscale, after Roy pairs and
approves it by typing. A second laptop runs one today. Roy now wants his Android
phone to join the same way, offering its own functions to the voice: its status,
finding it, notifications, location, phone calls, playback, and later the camera
and sensors.

What holds today:

- **Agents already run plugins.** `build_agent` starts the plugins in the
  machine's own settings file, approved there by typing their name, and offers
  their tools to the hub. Roy approves them again for that machine.
- **The agent protocol is plain MCP over HTTP** on the tailnet address. It has a
  pairing code, a long key per hub, and a Tailscale identity check on each call.
  It is not written down anywhere except in the code, and it carries no version.
## Options considered

**How the phone joins:**

- **It stays a client of the hub, through the companion page.** Not taken: a
  page cannot run with the screen off, place calls or read location in the
  background.
- **Termux running the Python agent.** Not taken: the `tailscale` command the
  agent checks callers with is not available to it, and it reaches the phone's
  audio and calls only second-hand. It may help during development, never as the
  agent.
- **A native app that speaks the same agent protocol.** The hub then treats the
  phone like any machine. **Chosen.** How it is built is claude-voice-mobile's
  record.

**Where the protocol is specified:**

- **In the app's repository.** Not taken: the hub would follow a private
  repository's drafts.
- **Here, beside the hub that serves it.** **Chosen.**

## Decision

**A phone is one more machine.** It is paired, approved and named like
`laptop-b`, for example `roy-phone`. Its tools reach the voice as
`roy-phone_<tool>`. It runs no Claude Code sessions; it offers plugins only.

**The protocol is written down and versioned.**

- It lives at `docs/agent-protocol.md` in this repository, with JSON schemas
  for its messages.
- It is drafted by the mobile project's architect session and reviewed here.
- Nothing is built on a version until it is frozen. A frozen version is never
  edited; a change is a new version.
- The hub refuses an agent that speaks a version it does not know.

**One plugin format on every machine.** A plugin is an MCP server with a name,
its tools and their inputs. That is the same on a laptop and a phone; only the
hardware behind it differs. Each plugin is approved on its own, first on the
machine and then on the hub. A plugin whose tools take new inputs is approved
again, as today.

**Built in small pull requests, each one useful alone:**

1. This record, and ADR-CVM-003 in claude-voice-mobile.
2. The audio-focus prototype there, measured.
3. `docs/agent-protocol.md` and its schemas, version 1, frozen.
4. The hub: refusing unknown protocol versions.
5. The app's core and its plugins, there, in their order.

## Consequences

- **The hub stays the same for every machine.** A phone, a laptop and a Pi are
  told apart by their plugins, not by the protocol.
- **Worse:** writing the protocol down means changing it is slower. A new
  version has to be drafted, reviewed and frozen before either side uses it.

## Related

ADR-CV-006 (machines and agents), ADR-CV-003 (risky actions wait for a spoken
yes), ADR-CVM-003 in claude-voice-mobile (the app's internal design),
`src/claude_voice/agent.py`, `src/claude_voice/plugins.py`,
`docs/other-machines.md`.
