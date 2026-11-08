# ADR-CV-007: The phone joins as an Android app agent, and calls between machines are an optional plugin

## Status

Proposed 2026-11-08. The architecture was settled with Roy Kollen Svendsen by
voice on 2026-11-08. Nothing beyond the first prototype is built until he accepts
this record.

## Context

ADR-CV-006 made every extra machine a small claude-voice agent. The hub (the
laptop running the bridge) dials each one over Tailscale, after Roy pairs and
approves it by typing. Lukas's laptop runs one today. Roy now wants two more
things:

1. **His Android phone as a machine.** It would offer the phone's own functions
   to the voice: its status, finding it, notifications, location, ordinary phone
   calls, playback, and later the camera and sensors.
2. **Voice calls between machines, started by voice**, such as "connect me to
   Lukas" and "hang up".

What holds today:

- **Agents already run plugins.** `build_agent` starts the plugins in the
  machine's own settings file, approved there by typing their name, and offers
  their tools to the hub. Roy approves them again for that machine. A second
  laptop needs nothing new.
- **The agent protocol is plain MCP over HTTP** on the tailnet address. It has a
  pairing code, a long key per hub, and a Tailscale identity check on each call.
  No Python is needed on the far side; ADR-CV-006 foresaw a native app for the
  phone.
- **A call between the laptops worked yesterday, by hand.** `parec` on one
  machine recorded 16 kHz mono sound and piped it over SSH into `pacat` on the
  other. Both are PipeWire's command-line tools. It went one way only, over a
  shell key that reaches everything, and nobody was warned before the speaker
  opened.
- **Android keeps background work short.** These are vendor facts, to be
  measured before they are relied on:
  - Doze stops background apps while the screen is off.
  - From Android 11, the microphone works in the background only from a
    foreground service of the microphone type.
  - From Android 14, each foreground service declares what it is for: location,
    microphone, camera, media playback or phone call.
- **Two apps can play sound at once if they agree to.** Android's audio focus
  lets an app ask others to lower their volume ("duck") rather than stop. Whether
  the Claude app's voice mode asks for focus in a way that lets music duck is
  unknown, and is the riskiest assumption in this record.
- **Roy's opencode-mobile app already makes WebRTC calls.** WebRTC is the
  browser's own call technology. The app opens a call to a GPT-Live model from a
  hidden web view. Its pieces fit this record's calls: echo cancellation, the
  offer and answer, a heartbeat, hang-up and mute.

## Options considered

**How the phone runs its agent:**

- **Do nothing; the phone stays a client of the hub through the companion page.**
  Not taken: the page cannot run with the screen off, place phone calls or read
  location in the background.
- **Termux, a Linux userland, with its Termux:API add-on.** The same Python agent
  would run there. Not taken:
  - The `tailscale` command the agent checks callers with is not available to it.
  - The Claude Agent SDK has no Android build.
  - The microphone, phone calls and audio focus are only reachable second-hand.
  - It hangs on a developer setting that a phone update can undo.
- **A native Android app in Kotlin, with a foreground service.** It speaks the same
  agent protocol, and uses Android's own APIs for audio focus, calls, location,
  notifications and the camera. It has proper permission prompts, and a visible
  notification while it runs. **Chosen.**

**Where the app's code lives:**

- **In this repository.** Not taken: a Kotlin build would weigh on every Python
  change here, and the other way round.
- **In a new repository, `roykollensvendsen/claude-voice-mobile`, private for
  now.** It keeps the same standard as claude-voice and claude-voice-ui. **Chosen.**
  The protocol and the plugin format stay specified here, so the hub and the app
  cannot drift apart in private.

**How call sound travels:**

- **The hub pipes it over SSH, as yesterday.** Not taken: it needs a shell key
  that can do anything.
- **Directly between the two agents** over Tailscale. This gives the shortest
  path and the least delay. **Chosen, as one of two.**
- **Through the hub**, which relays it. This reaches a machine the caller cannot
  reach directly, such as a phone on mobile data. **Chosen as the second.**

## Decision

**The phone is one more machine: an Android app that is an agent.** It is paired,
approved and named like `lukas-laptop`, for example `roy-phone`. Its tools reach
the voice as `roy-phone_<tool>`. It runs no Claude Code sessions; those stay on the
laptops. It is a plugin, sensor and sound agent.

**A small core with plugins around it.** The core holds:

- pairing;
- listening on the tailnet address only;
- the hub's key;
- the typed approval of each plugin, on the phone and again on the hub;
- the log of every call.

Each phone function is its own plugin inside the app, switched on and approved on
its own. A plugin that fails takes nothing else down.

**Who builds what.** The mobile repository is run like math-quest, but smaller:

- An **architect** session owns the hub-to-phone protocol and its schemas.
  - The protocol carries a version number, and is frozen before anything is built
    on it.
  - It also owns the layer rules, enforced in CI: the core never depends on a
    plugin, and no plugin reaches another.
- An **Android developer** session builds the app.
- **This repository's session owns the hub side.**
- Each session owns and develops its own skill.
- **Merging and escalation:** a change is merged when its checks are green.
  Only what matters goes to Roy, as an issue labelled `needs-roy`, tracked on a
  board and in a `STATUS.md`.

**Features, in this order:**

1. **Phone status and find-phone.** Battery, charging, network, volume, and a loud
   ring.
2. **Notifications from sessions.** An approval a session waits for shows on the
   phone, and can be answered there.
3. **Location.**
4. **Ordinary phone calls**, started and ended on the phone by voice, and also from
   the hub.
5. **Podcasts and local music played on the phone**, from two separate optional
   hub plugins: Audiobookshelf for podcasts, Navidrome for music.
6. **Audio calls between machines**, as an optional plugin.
7. **The camera and other sensors**, last.

**The hard requirement: talking to Claude and listening to music do not disturb
each other.** When the Claude app speaks or listens, music and podcasts are
lowered, never paused, and come back after. This is tested first, before any
other feature, with a small prototype app:

- one player holding media audio focus while Claude's voice mode runs;
- measured with and without a headset, wired and Bluetooth.

If Android cannot do this with the Claude app, the record is reopened before
anything else is built.

**The microphone is set per machine, and only on that machine.**

- There are two settings: *must answer*, where the person there accepts first,
  and *opens after the warning sound*.
- The warning sound always plays, before the microphone opens, under either
  setting.
- The setting can be changed only on the machine itself, never from the hub or by
  voice.

**Location.**

- There are two modes: a single reading on request, and logging over time.
- Logging is off by default, is switched on only on the phone, and its log can be
  deleted there.
- Every reading is logged with its time and caller.

**Ordinary phone calls from the hub.**

- A list of Roy's closest people can be called straight away.
- Any other number needs Roy's spoken confirmation, as risky actions do
  (ADR-CV-003).
- The list is kept on the phone and edited only there.

**Audio calls between machines are an optional plugin, `call`.** It is off unless
installed and approved. It supports both direct and through-the-hub sound. Both
sides get a one-time token from the hub, and each call is logged with who, when,
how long and how it ended. Nothing is recorded.

**The camera shows that it is on.** While it is in use there is always a visible
signal on the phone: Android's own indicator, and the app's notification.

**Built in small pull requests, each one useful alone:**

1. This record, in claude-voice.
2. The new repository, set up to standard, with the prototype plan.
3. The audio-focus prototype, measured, with its findings written down.
4. The protocol pieces the app needs, specified here: the agent protocol page and
   the plugin format.
5. The app's core: pairing, approval, logging, and phone status with find-phone.
6. Then each feature above, in its order, one pull request at a time.

## Consequences

- **The phone gets Android's own permissions and indicators**, and a foreground
  notification Roy can see. That is clearer than any Linux layer on top.
- **Two codebases now share one protocol.** The specification here is what keeps
  them in step, so it must be written before the app relies on it.
- **Calls cost nothing for anyone who does not want them.** The plugin is not
  installed, listed or running.
- **The warning sound is a promise the code must keep.** On every machine, a test
  must show that the microphone cannot open before the sound has played.
- **Worse:** a native app is weeks of work, not days. It has to be built, signed
  and installed outside the Play Store while the repository is private.
- **Worse:** whether music ducks under the Claude app is up to the Claude app. If
  the prototype shows it does not, the hard requirement cannot be met by this
  app alone.
- **Worse:** through-the-hub calls cost the hub's upload bandwidth and add delay.
- **Worse:** a call to a person who has not chosen to take it is still an
  intrusion, even with a warning sound. The *must answer* setting and the
  on-machine-only rule keep that in the owner's hands, not the voice's.

## Related

ADR-CV-006 (machines and agents), ADR-CV-003 (risky actions wait for a spoken
yes), `src/claude_voice/agent.py`, `src/claude_voice/plugins.py`,
`docs/other-machines.md`.
