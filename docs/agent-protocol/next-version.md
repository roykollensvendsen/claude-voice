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
  `agent_info` should say which check the machine makes, `tailscale-whois` or
  `pinned-address`, so the owner sees it when approving. A pinned address is
  the weaker check, so the key stays required on every request.
- **Refusal codes version 1 has no name for.** Version 1's `code` is open,
  so an agent may send them today, but a hub reads them only as "a refusal".
  Version 2 should name them, so a hub can say what to do:
  - `not_kept`, with `500`, from `POST /pair`: the code was right, but the
    machine could not save the pairing. The hub says to free space on the
    machine and pair again. It was never paired, and no key was given.
  - `bad_request`, with `400`, from `POST /pair`: the body was not a pairing
    request. It does not count as a wrong code.
  - `late`, inside MCP: the call started, but ran past the agent's limit. It
    may still finish, and its `call_id` stays kept, so sending it again gets
    `duplicate_call`. The hub says it may have happened, as for a timeout.
  - `not_run`, inside MCP: the agent was too busy to start the call within its
    limit, so it never reached the plugin. Its `call_id` is freed, so the hub
    may send the same call again, and it runs.
- **Five wrong pairing codes are a nuisance, not a breach.** Someone on the
  tailnet can end the pairing window by sending five wrong codes, and the
  owner then shows a new code. A guess cannot succeed: a code has 31 to the
  power of 8 values and lasts ten minutes. Version 2 should say so, so no one
  reads the limit as a guard against guessing.
- **The owner, not a name.** The text of `confirms` in `plugin.json`, and the
  rest of the protocol's words, say "the owner" where they now name a person.
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
