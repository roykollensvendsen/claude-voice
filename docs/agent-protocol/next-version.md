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
- **A yes from the phone's own screen.** Version 1 lets only the voice confirm
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
