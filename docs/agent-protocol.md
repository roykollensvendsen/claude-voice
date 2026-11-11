# The agent protocol, version 1

**Status: draft.** It is frozen when Roy approves it. Nothing is built on it
before then. A frozen version is never edited: a change is a new version, with
schemas of its own. `tests/test_agent_protocol.py` holds each frozen file to the
bytes it was frozen with, listed in
[`agent-protocol/frozen.json`](agent-protocol/frozen.json).

This is how the hub, the laptop running the claude-voice bridge, talks to an
agent: the small claude-voice on any other machine, a laptop, a Pi or a phone.
Why it works this way is in
[ADR-CV-006](../decisions/ADR-CV-006-other-machines-through-the-laptop.md) and
[ADR-CV-007](../decisions/ADR-CV-007-a-phone-agent-and-voice-calls.md). Setting a
machine up is in [other-machines.md](other-machines.md).

Most of it is what the agent in this package already speaks, which this page
calls **version 0**. Version 1 adds three things: a version on every answer,
the plugin format, and what the privacy rules need on the wire. Each section
says which parts are new.

The words *must*, *must not* and *may* mean what they mean in RFC 2119. Names and
addresses here are examples: `laptop-a` is a hub, `phone-a` an agent,
`100.64.0.x` tailnet addresses, `192.0.2.10` an address off the tailnet.

## The network

- **Over Tailscale only.** The agent listens on its own tailnet address, in
  `100.64.0.0/10`, and on no other. It must refuse to start without one.
- **Plain HTTP**, port 8812 unless the hub's settings name another. Tailscale
  already encrypts and authenticates the connection between the two machines.
- **The `Host` header** must be the agent's tailnet address and port, and a
  request with an `Origin` header is refused. That stops a web page in a browser
  on the tailnet from calling the agent.
- **Time.** An agent should accept a connection within 2 seconds, or the hub
  reports it as asleep or offline rather than waiting. A call gets the timeout
  the hub's settings give that machine, 30 seconds unless set.

## The version (new in version 1)

- **Every request from the hub names the versions it accepts**, in an HTTP
  header: `Claude-Voice-Protocol: 1` today, `Claude-Voice-Protocol: 1,2` from a
  hub that also knows a version 2. A request with no header is from a version
  0 hub, and the agent accepts it until the same switch-over as below.
- **Every answer from the agent carries the one version it answers in**, such as
  `Claude-Voice-Protocol: 1`: the highest version both speak. That covers
  pairing answers, MCP answers and refusals alike, so the hub can refuse an
  agent before any MCP is spoken.
- **When they share no version, the agent answers `400`** with
  [`error.json`](agent-protocol/v1/error.json), `code` `unsupported_version`,
  and `versions`: the versions it speaks.
- **No header means version 0**, the protocol as the agent in this package
  speaks it before version 1: everything on this page except the parts marked
  new.
- **`agent_info` also says `"protocol": 1`**, for people to read. If it and the
  header disagree, the header wins.
- **The hub refuses an answer in a version it did not offer.** It tells Roy
  which machine, which versions it speaks, and which the hub knows.
- **A receiver ignores what it does not know.** In every message, in every
  version, a field a receiver does not know is ignored, never refused. A later
  version may add fields, and new values to the lists that say so here (`uses`,
  the microphone setting and an error's `code`); it never removes a field or
  changes what one means. So a newer phone or hub never breaks an older one.
- **Version 0 is accepted until a switch-over Roy decides.** Freezing version 1
  and refusing version 0 are two separate events, and each is Roy's decision.
  Until the second, hub and agent each accept a peer that sends no header, so
  freezing this version cuts no machine off. After it, both refuse one.

## Pairing

Pairing gives the hub a key that this agent answers alone, typed for by Roy at
both ends.

1. On the agent's machine, its owner asks for a code. It is eight characters
   from `ABCDEFGHJKMNPQRSTUVWXYZ23456789`, which leaves out letters and digits
   that sound or look like one another. It works **once, for ten minutes**. A
   new code ends the old one. Five wrong codes end the pairing.
2. Roy types that code on the hub. The hub sends it:

   `POST /pair` with
   [`agent-protocol/v1/pair-request.json`](agent-protocol/v1/pair-request.json).

3. The agent asks Tailscale who is calling, from the caller's address. A caller
   off the tailnet gets `403`. A wrong or late code gets `403`. Both bodies are
   [`agent-protocol/v1/error.json`](agent-protocol/v1/error.json).
4. To the right code in time, the agent answers `200` with
   [`agent-protocol/v1/pair-response.json`](agent-protocol/v1/pair-response.json):
   a new random key of at least 32 bytes, and the hub's name as Tailscale gave
   it. It keeps only a hash of the key, and the hub's Tailscale node: its
   stable id, which does not change when the machine is renamed or readdressed.
   A machine pairs with one hub. Pairing again replaces the hub before.
5. The hub reads `tools/list` and `agent_info` with the new key, shows Roy what
   the machine offers, and Roy types the machine's name to approve it. The hub
   keeps the key readable by its owner alone, and the approval: the machine's
   stable node id, its address, and what it offers.

## Every later request

Two checks, by both sides, on every request. Neither alone is enough.

- **The agent** answers only a request that has
  `Authorization: Bearer <key>` with the paired hub's key, **and** comes from
  the address Tailscale says is the paired hub's node. Anything else gets `401`,
  `WWW-Authenticate: Bearer` and an
  [`error.json`](agent-protocol/v1/error.json) body.
- **Refusals carry a `code`** for the hub to read, besides the words in `error`.
  Version 1 knows `wrong_code` (a wrong, late or used-up pairing code),
  `not_on_tailnet`, `unauthorized` (not the paired hub's key or node),
  `unsupported_version` and, inside MCP, `duplicate_call` (below). A hub treats a code it does not know as a refusal.
- **The hub**, before each connection, asks Tailscale who is at the machine's
  address, and talks to it only if it is the node Roy approved.
- **The hub compares what the machine offers with what Roy approved**, on each
  connection, and calls nothing while they differ. What is compared is a
  SHA-256 of the canonical JSON (keys sorted) of each tool's name and input
  schema from `tools/list`. In version 1 it also covers each plugin's `name`,
  `tools` and `uses` from `agent_info`. A new tool, a tool that takes new
  inputs or a plugin that uses something new needs Roy's approval again.
- **Moving to version 1 asks Roy once per machine.** The larger fingerprint
  differs from the one he approved under version 0. At a machine's first
  version 1 connection the hub calls nothing on it until Roy has approved it
  again by typing its name.

## MCP

Behind the gate the agent is an ordinary MCP server, over MCP's streamable HTTP
transport at `/mcp`. It may answer in JSON or as a stream of events; it may keep
a session with `Mcp-Session-Id` or keep none. The hub uses only:

- `initialize`, and the agent's `instructions` in its answer, which the hub
  passes on to the voice;
- `tools/list`;
- `tools/call`.

The agent must not ask the hub anything back: no sampling, no elicitation, no
roots. A question for a person goes through the
[spoken yes](#a-spoken-yes-new-in-version-1) below.

**Tool names.** A plugin's tool is named `<plugin>_<tool>`, such as
`status_battery`. The hub offers it to the voice as `<machine>_<plugin>_<tool>`,
such as `phone-a_status_battery`. A machine name is lowercase letters, digits and
hyphens, starting with a letter, at most 32 characters. A plugin name is
lowercase letters, digits and underscores, the same way.

**Answers.** A tool answers with `structuredContent`, an object. An answer the
voice should read aloud puts the words in a field `say`. A refusal is an answer
with `isError: true` and its reason as text. An agent may answer with text only;
the hub then reads text that is a JSON object as that object, and anything else
as `{"text": ...}`.

**Who asked (new in version 1).** The hub puts who asked into each
`tools/call` request's `_meta`, as
[`agent-protocol/v1/caller-meta.json`](agent-protocol/v1/caller-meta.json): `voice`,
or the name of a Claude Code session. The agent logs every call with its time,
its tool, the hub's node and this caller. **The log stays on the machine**, and
for a tool whose plugin `uses` anything private it never holds the call's
inputs or answer, so the log cannot become a record of where Roy has been or
whom he called.

**A call is run once (new in version 1).** A call can be cut off after it has
reached the agent, by the hub's timeout or a dropped connection, and the agent
may still finish it.

- The hub puts a new random id for each call in the same `_meta`, as
  `claude-voice/call_id`. It sends that id again only with the same call, when
  it sends that call again.
- An agent keeps the ids it has run for at least ten minutes, longer than any
  timeout. It refuses an id it has already run, with `isError: true` and a
  structured answer that fits [`error.json`](agent-protocol/v1/error.json),
  with `code` `duplicate_call`. The call is never run twice.
- A call without an id, from a version 0 hub, is run as before.
- **A call that times out is not reported as failed.** The hub tells the voice
  that it *may have happened*, so the voice says that it does not know and
  does not ask again as if nothing was done. The hub does not send the call
  again by itself.

### `agent_info`

Every agent offers the tool `agent_info`, taking nothing. Its answer is
[`agent-protocol/v1/agent-info.json`](agent-protocol/v1/agent-info.json): the
protocol version, the machine's name and address, its hub, the tools it offers,
its software version, and, new in version 1, its plugins and privacy settings.

### Sessions

An agent with sessions turned on also offers the session tools of the bridge
under their own names: listing, reading, messaging, starting, renaming and
stopping that machine's Claude Code sessions, and answering what they ask. The
list is `SESSION_TOOLS` in `src/claude_voice/agent.py`. They belong to no
plugin, so they appear in `agent_info`'s `tools` and in no plugin's entry. A
phone offers none.

## The plugin format (new in version 1)

A plugin is an MCP server on the machine: a name, its tools and their inputs.
That is the same on a laptop and a phone; only the hardware behind it differs.
On the wire it is:

- **its tools**, in `tools/list`, named `<plugin>_<tool>` with their input
  schemas and descriptions;
- **its entry in `agent_info`**,
  [`agent-protocol/v1/plugin.json`](agent-protocol/v1/plugin.json): its name,
  which of the listed tools are its, its own rules for the voice, and `uses`:
  which private things on the machine it can reach (`microphone`, `camera`,
  `location`, `calls`, `contacts`, `notifications`, `media`, `sensors`, and
  `usage`: the use of the machine and its apps, such as mobile data used). A
  later version may add values. A hub shows one it does not know to Roy as
  *unknown*, with its name, and it needs his approval like any other.

Each plugin is approved on its own, first on its machine and then on the hub,
and `uses` is shown to Roy when he approves it. `uses` follows the plugin's
settings as they are now: turning on a setting that reaches more, such as
naming a home Wi-Fi, which needs `location`, changes it, and the hub calls
nothing on that machine until Roy has approved it again.

## The privacy rules on the wire (new in version 1)

The privacy rules in ADR-CV-007 are kept by each machine. The hub relies on
them and cannot change them. What travels between the two:

- **The settings are reported, never written.** `agent_info` carries
  [`agent-protocol/v1/privacy.json`](agent-protocol/v1/privacy.json): the
  microphone setting (`none`, `must-answer` or `after-warning`; a hub shows a
  value it does not know as *unknown*, and treats it as one that may open a
  microphone) and whether location is logged over time. No tool an agent offers may take the microphone
  setting, location logging, the list of people who may be called straight away,
  or the camera as an input. Those are changed on the machine itself, and a
  test on each machine proves it.
- **A warning sound plays before a microphone opens**, under either setting.
  Nothing on the wire turns it off.
- **A tool that opens the microphone or the camera, or reads location, says so
  in its plugin's `uses`.** The agent logs every such call with its caller, as
  above, without its inputs.
- **The report has to agree with the plugins.** An `agent_info` in which a
  plugin `uses` the microphone while `privacy.microphone` is `none` breaks this
  rule, and the hub refuses that machine. That is a refusal for a broken rule,
  not for something the hub does not know: a field or value it does not know is
  ignored or shown as unknown, as above. Every machine reports
  `location_log`, whether or not a plugin uses location.

### A spoken yes (new in version 1)

Some calls wait for Roy to say yes aloud, as ADR-CV-003 sets: calling a number
that is not on the phone's own list, for one. The plugin decides which; the hub
cannot skip the question.

1. The tool answers
   [`agent-protocol/v1/needs-yes.json`](agent-protocol/v1/needs-yes.json): the
   question to read aloud, a token, the tool to confirm with, and how many
   seconds the token works, at most 120.
2. The voice reads the question exactly. Only if Roy clearly says yes does it
   call the named tool with
   [`agent-protocol/v1/confirm-request.json`](agent-protocol/v1/confirm-request.json).
3. The hub calls the tool in `confirm_with` only if it belongs to the same
   plugin as the tool that asked: the same `<plugin>_` in front. The plugin, not
   the hub, holds the token and decides what it confirms.
4. A token works once, for that question only. A yes is passed on as a yes,
   never as a standing permission.

## Not in version 1

- **Calls between machines**, the `call` plugin of ADR-CV-007, and their
  one-time tokens. They come in a later version, once the audio-focus prototype
  has been measured.
- **The agent dialling the hub**, for a phone the hub cannot reach. ADR-CV-006
  leaves it for when it is needed.
