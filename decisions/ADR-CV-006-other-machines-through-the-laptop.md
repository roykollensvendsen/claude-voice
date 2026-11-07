# ADR-CV-006: Reach other machines through the laptop, with a small agent on each

## Status

Accepted by Roy Kollen Svendsen, 2026-11-07. Proposed 2026-11-05.

## Context

The bridge runs on one computer, the owner's laptop, and the voice reaches only
the sessions and plugins there. He wants the same reach on his other machines:
the work PC, a Raspberry Pi in the rack, and later his Android phone.

What holds today:

- **The voice talks to one MCP server.** The Claude app and the voice app each
  hold one connector, signed in once with the bridge's OAuth (ADR-CV-002).
- **Every machine is on the owner's tailnet.** Tailscale encrypts and
  authenticates each connection with the machines' WireGuard keys. It will name
  the machine and user behind any tailnet address (`tailscale whois`). The
  bridge already admits callers only from this computer or the tailnet
  (`api.from_tailnet_or_here`).
- **Plugins (#96) already reach another MCP server.** The bridge starts it, lists
  its tools, offers only the ones approved, adds the plugin's instructions, cuts
  off slow calls and starts it again after a failure. The owner approves the
  fingerprint of what will run by typing its name.
- **Machines sleep.** The laptop closes, the PC suspends, and the phone is
  dozing most of the day. A call that waits on a sleeping machine leaves the
  voice silent until the Claude app gives up, after about 30 seconds.

## Options considered

- **Do nothing; one bridge per machine, one connector each.** Each connector
  needs its own sign-in and its own public address. The voice would have to
  know which connector holds which machine. Not taken: requirement 2 says the
  voice talks only to the laptop.
- **Remote shells: the hub runs `ssh pi claude ...`.** No new program on the
  far side, but every action becomes a shell on a machine with a key that
  reaches everything. There is no allow-list and no phone. Not taken: least
  privilege is lost.
- **The agent dials the hub and keeps a connection open.** This works behind any
  network and suits a phone that cannot be reached from outside. But the hub
  needs a server for agents next to its own, and a new protocol to carry MCP
  inside it. Not taken *yet*: the tailnet already makes every machine
  reachable, so the simpler direction comes first (see Consequences).
- **The hub dials the agent; the agent is an MCP server on its own tailnet
  address.** A remote agent is then a plugin over HTTP instead of stdio, and
  the plugin code carries over: listing, allow-list, instructions, timeouts,
  restarts, health. **Chosen.**

## Decision

**One machine stays exactly as today.** Without a `[machines]` section, nothing
starts, nothing is offered and nothing changes. The agent is a command only the
extra machines run.

**The laptop is the hub.** The voice connects to it alone. Every other machine
is reached through it.

**Each extra machine runs `claude-voice agent`.** This is the same package, as a
small MCP server listening on that machine's tailnet address only, never on all
addresses. It offers what its own settings file allows, and nothing else:

- its plugins, approved on that machine by its owner, as today;
- its Claude Code sessions, if `sessions = true`: listing them, reading their
  output, and sending them a message.

It holds its own plugins' secrets and passes on only the tools' answers.

**Pairing and approval, typed by Roy.**

1. On the new machine, `claude-voice agent pair` shows an eight-character code
   for ten minutes.
2. On the hub, `claude-voice machines approve pi` asks for that code. It shows
   the machine's tailnet name and owner (from `tailscale whois`) and the tools it
   offers. Roy types the code, and then the machine's name.
3. The hub sends the code to the agent, which answers with a long random key
   meant for that hub alone. Each side stores its half, readable only by its
   owner.

From then on both sides check both things on every call: the Tailscale identity
of the other machine, and the key. That makes the authentication mutual, and
neither check alone is enough. As with plugins, the approval is a fingerprint of
the machine's node, its address and its tool list. A machine that starts
offering more must be approved again.

**Names say which machine.** A remote tool is offered as `<machine>_<tool>`, for
example `pi_garage_status`. A remote session is named `<machine>/<session>`, for
example `pc/billing`, and the existing session tools route on that prefix. Every
call is logged with its machine, like any other tool call today.

**Reachability, so the voice never hangs.**

- Connecting gets 2 seconds, and a call gets the machine's own timeout.
- Both are capped by the caller's time budget, as today.
- Looking across machines (`fleet_recap`, `list_active_sessions`) asks every
  machine at once. A machine that does not answer in time is reported, not
  waited for.
- `health` and `fleet_recap` show each machine as one of:
  - `online`, with its latency;
  - `slow`, when it answered last time but not this time;
  - `asleep` or `offline`: Tailscale says the node is not connected, and when it
    was last seen;
  - `agent down`: the node is up, but nothing answers on the agent's port;
  - `not approved`.

**Least privilege.**

- An agent offers only what its own file lists.
- The hub offers only what Roy approved for that machine.
- The hub never holds a remote plugin's secret or a remote Claude Code login.
- A remote session's approvals stay on its machine. A spoken yes is passed on
  as a yes, never as a standing permission.

**Built in small pull requests, each one useful alone:**

1. This record.
2. The agent serves its plugins over the tailnet with a key, and it is tested
   against a stand-in hub.
3. The hub: `[machines.<name>]`, pairing, typed approval, and remote tools with
   their machine's prefix. This reuses `plugins.Plugin`, with a second way to
   connect.
4. Each machine's state in `health` and `fleet_recap`, the 2-second connect, and
   asking all machines at once.
5. Remote sessions: `<machine>/<session>` for listing, reading and messaging.
6. The work PC and the rack Pi, by instructions only.
7. The phone, later and on its own: an Android app with a foreground service
   that answers the same small MCP over the tailnet. Being plain MCP, the agent
   protocol needs no Python on the far side.

## Consequences

- **A remote machine is a plugin.** Most of the hard parts are already tested,
  and a fix to one helps both.
- **The voice sees more tools.** Each approved remote tool is one more entry in
  `tools/list`, so the allow-list per machine is what keeps the list short.
- **Worse:** a machine the hub cannot dial cannot be reached. That is a phone
  on mobile data with Tailscale paused, or a PC behind a network that blocks
  Tailscale's direct path. The phone agent may need the agent-dials-hub
  direction after all. That would be a second way to connect, and it would
  change none of the approval or naming.
- **Worse:** a sleeping machine is not woken. The voice hears `asleep, last seen
  at 14:02` rather than an answer. Wake-on-LAN is deferred, until a machine that
  should answer keeps sleeping.
- **Worse:** another program on the hub could call an agent too, if it found the
  key file. The file is readable only by its owner, and the key is never written
  anywhere else, but it is the same trust the bridge's own token has today.
- **Two settings files change in step.** A tool approved on the far machine still
  needs approving on the hub. That is two typed approvals for one remote tool,
  on purpose.

## Related

`src/claude_voice/plugins.py`, `src/claude_voice/api.py` (`from_tailnet_or_here`),
ADR-CV-002 (the one sign-in), ADR-CV-004 (messaging sessions, which remote
sessions keep).
