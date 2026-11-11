# For the next version of the agent protocol

Version 1 is frozen, so a change to what travels between the hub and a machine
waits here for version 2. Each line says what, and why. Nothing here is decided
until the owner approves version 2.

- **The hub never offers a tool that unmutes a machine's microphone.** Muting
  may come from the voice. Unmuting is done on the machine itself, as the
  microphone setting is. The phone app already enforces it: the mute is one of
  the settings only the phone changes, and no tool may take one as an input.
  This line makes the same rule hold on the wire, for every machine.
- **Calls between machines**, the `call` plugin of ADR-CV-007, and their
  one-time tokens, once the audio-focus prototype has been measured (named in
  version 1 as left out).
- **The agent dialling the hub**, for a phone the hub cannot reach (named in
  version 1 as left out; ADR-CV-006 leaves it for when it is needed).
