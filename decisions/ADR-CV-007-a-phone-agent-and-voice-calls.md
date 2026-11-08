# ADR-CV-007: An agent on the phone, and voice calls between machines as an optional plugin

## Status

Proposed 2026-11-08 by Roy Kollen Svendsen's assistant, at his request. Nothing is
built until he accepts it.

## Context

ADR-CV-006 made every extra machine a small claude-voice agent. The hub (the
laptop running the bridge) dials each one over Tailscale, after Roy pairs and
approves it by typing. Lukas's laptop runs one today. Roy now wants two more
things:

1. **His Android phone as a machine.** It would offer phone functions to the voice
   as plugins: location first, later notifications and battery, and more.
2. **Voice calls started by voice**, such as "connect me to Lukas" and "hang up".
   They would run between the laptops, and later with the phone too.

What holds today:

- **Agents already run plugins.** `build_agent` starts the plugins in the
  machine's own settings file, approved on that machine by typing their name. It
  offers their tools to the hub, which Roy approves again for that machine.
  Plugins run with sessions turned on as well. Nothing is missing for a laptop.
  The phone is what lacks a way to run the agent at all.
- **A call between the laptops worked yesterday, by hand.** `parec` on one machine
  recorded 16 kHz mono sound and piped it over SSH into `pacat` on the other. Both
  are PipeWire's command-line tools. It went one way only, over a shell key that
  reaches everything, and Lukas was not warned before his speaker opened.
- **Android keeps background work short.** These are vendor facts, and each will
  be measured before it is relied on:
  - Doze and app standby stop background apps while the screen is off.
  - From Android 12, the system kills a misbehaving app's extra processes (the
    "phantom process" limit).
  - From Android 11, an app may use the microphone only while it is on screen, or
    from a microphone foreground service started while it was.
  - Only one app usually records at a time. The Claude app's voice mode takes the
    microphone while it listens.
- **Tailscale on Android is a VPN app.** Other apps on the phone, Termux among
  them, see its tunnel address. Whether they can listen on it with the screen off
  is the first thing to measure.

## Options considered

**How to run the phone agent:**

- **Do nothing; the phone stays a client of the hub.** Calls would then work only
  through the companion page (below), and there would be no location. Not taken
  on its own, but the page is the first step either way.
- **Termux, with the Termux:API add-on, and Termux:Boot to start at boot.** Termux
  is a Linux userland. It runs Python, so the same `claude-voice agent` package
  and the same plugin code. Termux:API is a set of commands such as
  `termux-location`, `termux-notification`, `termux-tts-speak`,
  `termux-battery-status` and `termux-microphone-record`. A phone plugin is then a
  small MCP server calling those commands. `termux-wake-lock` and excluding Termux
  from battery optimisation keep it running.
  - For: no new codebase, one agent, the plugin model as on the laptops, a
    prototype within days.
  - Against: it must be installed from F-Droid (the Play Store build is
    outdated). Some Python packages may need compiling there; that needs
    measuring. The phantom-process limit needs a developer setting, or an
    `adb` command, to switch off. Background microphone use goes through the
    Termux:API app, and how reliably that works with the screen off is unknown.
  - **Chosen for the first phone step.**
- **A native Android app (Kotlin) serving the same small MCP.** ADR-CV-006 foresaw
  this. It has a proper foreground service with the location and microphone types,
  Android's own permission prompts and a notification while it runs.
  - For: it is the robust end state.
  - Against: a second codebase, signing and installing outside the Play Store,
    and weeks rather than days.
  - Not taken *yet*: it becomes worth it if Termux cannot stay reachable with the
    screen off, or cannot use the microphone in the background.
- **Tasker, or a home-automation companion app, as a go-between.** Not taken:
  more parts, closed tools, and nothing an MCP server can call directly.

**How calls are set up:**

- **The hub pipes the sound over SSH, as yesterday.** Not taken: it needs a shell
  key that can do anything, and nothing warns the other side.
- **A `call` plugin on each machine's agent, with the sound going directly
  between the two machines over Tailscale.** The hub only tells both sides to
  start and stop. **Chosen.**
- **WebRTC, the browser's own call technology, behind a button on the companion
  page.** The phone's browser asks for the microphone itself and shows that it is
  in use. It works today without any phone agent. **Chosen as the phone's first
  step.** It needs a WebRTC endpoint on the laptop side, inside the call plugin.

## Decision

**The phone is one more machine, for sensors and sound, never for sessions.** It
runs `claude-voice agent` in Termux, without `--sessions`. Claude Code sessions
stay on the laptops. The phone is paired and approved like `lukas-laptop` and
named, say, `roy-phone`. Its tools reach the voice as `roy-phone_<tool>`.

**The phone agent is a small core with plugins around it.**

- **The core** is what every agent already has: pairing, listening on the tailnet
  address only, the hub's key, the typed approval of each plugin, and the log of
  every call. It holds no phone function itself.
- **Each phone function is its own plugin**, a separate MCP server the core starts:
  `location`, `call`, `notifications`, `battery`, and later others. Each is listed in
  the phone's settings file and approved on its own, first on the phone and then
  on the hub.
- **A plugin that fails or is removed takes nothing else down.** The core restarts
  it with a growing pause, as `plugins.Plugin` does today.

**Its first plugin is location.** `roy-phone_where_am_i` reads the position once,
when asked. Nothing tracks in the background, and nothing is kept. Each reading
is logged with its time and caller, but not its coordinates. More phone plugins
(notifications, battery) each need their own approval, on the phone and on the
hub, as today.

**Calls are an optional plugin, `call`, off unless the owner adds and approves
it.** A person who never wants calls never has the code running. The plugin
offers:

- `call_start(who)`: the hub resolves `who` ("Lukas") to a machine through a
  `[call.people]` table in the hub's settings.
- `call_hang_up`.
- `call_status`.

**What happens on the called machine, in this order:**

1. **A chime, before anything else.** It plays out loud on the called machine,
   followed by a spoken line, "Roy is calling", and a desktop notification
   offering *Decline*.
2. **A pause of at least three seconds.** The pause can be lengthened in the
   settings and never shortened below three. *Decline*, or a hang-up from either
   side, ends the call here.
3. **Only then does that machine's microphone open.** The calling side opens its
   own microphone only after the called side says it has chimed.

**The rules every call keeps:**

- **Visible while on.** A notification stays up for the whole call on both
  machines, ending in a second, different chime.
- **Time-limited.** A call ends by itself after 30 minutes, unless renewed by
  voice.
- **Nothing recorded.** Each call is logged with who, which machines, when, how
  long and how it ended. No sound is ever stored.
- **Reached only through the paired hub.** Both sides get a one-time call token
  from the hub, and refuse sound from anyone without it.
- **Plain sound, protected by the tunnel.** Between laptops the sound is 16 kHz
  mono over a socket on the Tailscale addresses, as yesterday but both ways and
  without a shell. Tailscale encrypts it. Opus compression comes later if the
  data use matters.

### What is reused

- **The agent core runs unchanged in Termux, with two exceptions to fix first.**
  It is plain Python, and its own needs are the MCP library and `psutil`.
  - **It asks the `tailscale` command who is calling, and for its own address.**
    The Android Tailscale app has no command Termux can run. On the phone, the
    agent would take its address from the network interface. It would admit a
    caller by the hub's tailnet address together with the key. Tailscale
    already proves that address, but this is one check fewer than on a laptop,
    and the record says so.
  - **The package always pulls in the Claude Agent SDK.** The SDK is needed only
    for sessions, and has no build for Android. It would become an optional extra,
    so `pip install claude-voice` on the phone leaves it out.
  - Whether the MCP library's compiled parts (`pydantic-core`) install in Termux
    is measured in step 4, not assumed.
- **Plugins speak the same MCP on every machine.** A `call` plugin has the same
  tools, the same chime-first rule and the same tests on a laptop and a phone.
  Only the hardware layer differs: PipeWire's `parec` and `pacat` on a laptop, and
  `termux-microphone-record` and `termux-media-player` (or WebRTC) on the phone.
  The same goes for `location` (Termux:API on the phone; nothing on a laptop) and
  `notifications`.
- **The companion page, claude-voice-ui, is already on the phone** as an
  installable web app. It gets the call button, and a card for the phone itself:
  online or not, battery, and its plugins. These come from the machine rows it
  already reads.
- **From Roy's opencode-mobile app (read, not copied): its GPT-Live call screen.**
  It holds a WebRTC call in a hidden web view on a secure origin. The pieces fit
  the page's call button almost as they are:
  - the microphone opened with echo cancellation, noise suppression and automatic
    gain;
  - an offer that waits up to three seconds for its network candidates;
  - the answer from a server call;
  - a heartbeat that keeps the call alive;
  - hang-up and mute;
  - the transcript lines from the data channel.

  There the far end is OpenAI's model; here it would be the call plugin on a
  laptop. The app is Apache-2.0, which can be taken into this GPL project.

**Built in small pull requests, each one useful alone:**

1. This record.
2. The call plugin between two laptops, with chime, pause and decline, tested
   against stand-in audio.
3. The companion page's call button: WebRTC from the phone's browser to the call
   plugin on a laptop.
4. The agent core ready for the phone: the Claude Agent SDK as an optional
   extra, and its address and caller check without the `tailscale` command.
   Then a Termux recipe with `location` as its first plugin. It is measured
   first for how long it stays reachable with the screen off, and how much
   battery it uses.
5. The phone in calls through its agent. This comes only if step 4 shows the
   microphone works in the background. Otherwise the call button stays the phone's
   way in, and the native app is reconsidered.

## Consequences

- **One way to grow the phone.** Every phone function is a plugin with its own
  typed approval, so the phone offers exactly what Roy approved.
- **Calls cost nothing for anyone who does not want them.** The plugin is not
  installed, not listed and not running.
- **The chime is a promise the code must keep.** The plugin's tests must show that
  the microphone cannot open before the chime has played, the same way other rules
  here are guarded.
- **Worse:** a phone on mobile data with Tailscale paused cannot be dialled. The
  agent-dials-hub direction deferred in ADR-CV-006 may be needed for it.
- **Worse:** Termux depends on apps from F-Droid and on a developer setting. A
  phone update can break the agent without notice. The hub then reports the phone
  as `agent down` rather than hanging, as for any machine.
- **Worse:** the Claude app and a call both want the phone's microphone. Talking
  to Claude during a phone call will probably not work, and is not promised.
- **Worse:** a call with a person who has not chosen to take it is still an
  intrusion, even with a chime. The *Decline* button and the people table keep
  that in the owner's hands, not the voice's.

## Related

ADR-CV-006 (machines and agents), ADR-CV-003 (risky actions wait for a spoken
yes), `src/claude_voice/agent.py`, `src/claude_voice/plugins.py`,
`docs/other-machines.md`.
