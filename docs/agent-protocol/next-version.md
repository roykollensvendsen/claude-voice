# For the next version of the agent protocol

Version 1 is frozen, so a change to what travels between the hub and a machine
waits here for version 2. Each line says what, and why. Nothing here is decided
until the owner approves version 2.

- **The hub never offers a tool that unmutes a machine's microphone.** Muting
  may come from the voice. Unmuting is done on the machine itself, as the
  microphone setting is. The phone app already enforces it: the mute is one of
  the settings only the phone changes, and no tool may take one as an input.
  This line makes the same rule hold on the wire, for every machine.
- **A machine tells the hub that something happened.** In version 1 only the
  hub calls, so a phone cannot say that it is arriving at a saved place. A
  location trigger (claude-voice-mobile, `docs/design-location-triggers.md`)
  needs it: an event naming the trigger and nothing else, never a position.
  Without it, the hub would have to ask the phone every few seconds.
- **An agent that cannot ask Tailscale who is calling.** Version 1 has the
  agent ask Tailscale which node a request comes from. On Android, Tailscale's
  app answers no other app, so the phone cannot ask. Instead it keeps the
  tailnet address the hub paired from, and checks that address and the key on
  every request. A changed address means pairing again (claude-voice-mobile,
  ADR-CVM-009). Version 2 should allow that as a second way to meet the
  machine check, and say what the hub must do: pair from the address it will
  call from, and expect "pair again" after it moves.
- **A private thing named `messages`: "reads or sends the owner's text
  messages".** The phone's `sms` and `sms_send` plugins need it. Version 1's
  `uses` is open, so the name already travels, but its examples name only
  nine things. A hub that shows the owner what a plugin reaches should be
  able to name this one too. Text messages hold other people's words and
  one-time codes, so the owner should see this name before approving either
  plugin. The phone app accepts only the names the protocol lists, so it
  waits for this line.
- **Whether each private thing is allowed now, per plugin: `access`.** In
  version 1, `uses` says what a plugin may reach once approved. It does not say
  whether the machine allows it now. The owner can turn contacts off in the
  phone's settings, and the hub still sees `contacts` and cannot warn. Each
  plugin entry gets an `access` object. It has one key per name in `uses`, and
  each value is "granted", "refused" or "never_asked". "never_asked" means the
  machine never asked the owner. The same holds for an OS permission or an
  account login on a laptop. The rules:
  - `access` never adds to `uses`. A key not in `uses` makes the hub refuse
    the entry, as an unknown use does. A refusal changes `access`, never
    `uses`, so turning something back on needs no new approval.
  - Reporting it never asks the owner for anything. On Android it is read with
    `checkSelfPermission`. "never_asked" comes from the app's own record that
    it asked.
  - It holds plain states only, never anything read with the access, such as a
    contact.
  - The hub reads it with `agent_info`, on every connection. Android kills an
    app when a permission is taken away, so a refusal shows at the next
    connection. A permission given back may show late. So the voice may warn
    before a tool, and the voice may answer "which permissions does phone-a
    have?", but the tool's own answer stays the authority.

  Version 1 lets a newer sender add a field, so a phone may send it before
  version 2 and an older hub ignores it. Until then, a read-only tool on the
  phone can give the same answer within version 1.
- **Not wanted for now: a yes from the phone's own screen.** The owner chose a
  spoken yes only for location triggers on 2026-11-15, so this waits for the
  owner to ask again. Version 1 lets only the voice confirm
  a spoken yes. A location trigger may ask in a notification as well, for when
  the voice is not running, so a tap by the owner on the phone itself would
  count as a yes, under a caller name of its own. A notification can be tapped
  on a locked phone, by anyone holding it. So the tap counts only once the
  phone is unlocked, with its PIN, pattern or a fingerprint. And a yes from
  the phone never reaches a door or a lock, such as the garage door's.
- **Calls between machines**, the `call` plugin of ADR-CV-007, and their
  one-time tokens, once the audio-focus prototype has been measured (named in
  version 1 as left out).
- **The agent dialling the hub**, for a phone the hub cannot reach (named in
  version 1 as left out; ADR-CV-006 leaves it for when it is needed).
