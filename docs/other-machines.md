# Another machine

The voice talks to one computer, the hub: the one running the claude-voice
bridge. Any other computer you own can be reached through it. That computer runs
a small claude-voice of its own, the agent, and the hub calls it over Tailscale,
the private network between your own devices. Why it works this way is in
[ADR-CV-006](../decisions/ADR-CV-006-other-machines-through-the-laptop.md).

When it is set up, the voice gets that machine's tools with its name in front:
`lukas-laptop_list_active_sessions`, `lukas-laptop_start_active_session` and so on.

Its sessions are also in the hub's own lists, `list_active_sessions` and
`fleet_recap`, named with the machine in front: `lukas-laptop/hub-test`. The
session tools take that name, so "message lukas-laptop/hub-test" reaches it. The
same lists say whether each machine is online, slow, offline or not approved. A
machine that does not answer within two seconds is reported, not waited for.

## What keeps it safe

- **The agent listens on its Tailscale address only.** It refuses to start
  without one, so it is never reachable from the home network or the internet.
- **It answers one hub.** Pairing gives that hub a long key. Every call needs the
  key, from the machine Tailscale says that hub is. A stolen key is no use from
  anywhere else.
- **You approve each machine by typing,** on the hub: the code the machine
  shows, then its name. The hub calls it only while Tailscale says the same
  machine is at its address, and only while it offers the tools you approved.
  Anything new needs approving again.
- **It offers little.** Its own approved plugins, and with sessions turned on,
  that machine's Claude Code sessions: listing, reading, messaging, starting,
  renaming and stopping them, and answering what they ask. Nothing about the
  bridge itself, such as updating it or handing out access.
- **Secrets stay where they are.** The machine keeps its own Claude sign-in and
  its plugins' secrets. The hub holds only the key, readable by you alone.

## What you need

On the other machine: Tailscale, signed in to the same account as the hub, and
for sessions, Claude Code signed in (`claude`, then `/login`). tmux lets the voice
rename a session in Claude Code itself, but is not required.

## Set it up

**1. On the other machine,** install claude-voice and the agent. With
`--sessions`, its Claude Code sessions are offered too; they start in the
background, never in a window on that machine's screen.

<!-- not run: it installs a service on the machine it runs on -->
```
uv tool install git+https://github.com/roykollensvendsen/claude-voice
claude-voice agent install --sessions
```

It checks that Tailscale is up and Claude Code is signed in, starts the agent as
a user service, and prints the lines to add on the hub, with this machine's
address. The agent runs while you are logged in there. To keep it running when
nobody is, `sudo loginctl enable-linger $USER`.

**2. On the hub,** add what it printed to `~/.config/claude-voice/config.toml`.
The name is yours to choose:

```toml
[machines.lukas-laptop]
address = "100.86.231.118"
```

**3. Pair.** On the other machine, show a code. It works once, for ten minutes:

<!-- not run: it opens a pairing on the machine it runs on -->
```
claude-voice agent pair
```

On the hub, approve it: type the code, look at what it offers, type its name.

<!-- not run: it asks for typed input -->
```
claude-voice machines approve lukas-laptop
```

**4. Restart the bridge** on the hub, and reconnect claude-voice in the Claude
app so it sees the new tools.

## Checking it

On the hub, `claude-voice machines` lists each machine and whether it is
approved. If a call fails, the voice says why:

- **"did not answer"**: the machine is asleep, off, or the agent is not running.
  On the machine, `systemctl --user status claude-voice-agent`.
- **"now offers other tools than you approved"**: the agent was updated or its
  settings changed. Approve it again, as in step 3.
- **"is not the machine you approved"**: another machine is at that address.
  Check the address in the settings against `tailscale status`.

## Answering questions from its sessions

The voice can say yes or no only to sessions the agent's own bridge runs, the
ones started with `create_session` and given work with `send_task`. A session
started with `start_active_session` runs in the background and asks in the
Claude app, through Remote Control, as it does on the hub.

## Taking it away

On the other machine, `systemctl --user disable --now claude-voice-agent`. On the
hub, remove its `[machines.<name>]` section and restart the bridge.
