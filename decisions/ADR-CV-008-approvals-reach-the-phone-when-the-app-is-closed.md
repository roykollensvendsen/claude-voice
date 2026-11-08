# ADR-CV-008: Approvals reach the phone when the app is closed

## Status

Proposed, 2026-11-07. Roy asked for the proposal. It is his to accept before any
of it is built.

## Context

A Claude Code session that needs a yes shows a box in the terminal. The phone
hook (`phone_hook.py`, #64) hands that box to the bridge first. The bridge says
whether to wait for the phone, and today it says yes only while someone has the
companion app open (`HUB.listening()` in `api.py`). Otherwise the box shows in
the terminal at once.

What that cost in the five days to 2026-11-07, counted from the bridge's log:

- 66 prompts reached the bridge, and 5 were answered from the phone.
- 2 waited for the phone and timed out.
- The other 59 showed in the terminal, because the app was closed. The owner was
  often away from the laptop, talking by voice, and had to walk back to it.
- The voice's own `approve` and `deny` tools were called 0 times.

What holds:

- The companion app is a progressive web app, installed on the owner's Android
  home screen. A web app can receive Web Push notifications through a service
  worker while it is closed.
- Web Push payloads are encrypted end to end (RFC 8291). The browser's push
  service (Google's, for Chrome on Android) carries an opaque blob and learns
  only that something was sent to this phone.
- The hook blocks the session while it waits. Waiting for the phone while the
  owner sits at the laptop delays the box he would have answered at once.
- The laptop reports whether its screen is locked and whether it is idle
  (`loginctl`, `LockedHint` and `IdleHint`).

## Options considered

- **Do nothing.** It is the most common case and costs a walk each time. Not
  taken.
- **Always wait for the phone, for up to five minutes.** Every prompt at the
  laptop would then take five minutes unless answered on the phone. Not taken.
- **A third-party push service (ntfy.sh, Pushover).** It needs another app and
  account, and the service reads every message. Not taken.
- **Announce prompts by voice only.** The voice app hears about prompts through
  `whats_new`, but only during a call. It is a good second path, not the only
  one.
- **Web Push to the installed app, and wait only when the owner is away.**
  **Chosen.**

## Decision

**The bridge decides that the owner is away** when any of these holds, and
otherwise behaves as today:

- the app is open (as now);
- the laptop's screen is locked;
- the voice called the bridge in the last ten minutes;
- the laptop has been idle for more than five minutes.

**When he is away, the hook waits, and the bridge sends a Web Push notification**
to each phone that has subscribed:

- The text is short and names no secret: "billing-ab asks to run Bash" or
  "billing-ab has a question". The command itself shows only in the app, behind
  the login, as today.
- Tapping it opens the app on that prompt, with Allow and Deny, or the choices.
- It waits as today, up to five minutes. With no answer, the box shows in the
  terminal and the prompt is marked timed out.
- A prompt answered at the terminal in the meantime closes on the phone.

**Subscribing.** The app asks for notification permission once, from a button
the owner taps, never by itself. It sends its push subscription to the bridge
over the same signed-in API. The bridge keeps its VAPID key pair (the key that
signs Web Push messages) next to its database, readable only by its owner.

**The voice says it too.** During a call, `whats_new` already carries
`needs_approval`. The voice app's instructions should say to read it out and
offer `approve` or `deny`, with the rule of ADR-CV-003: only on the owner's
clear yes.

**Built in small pull requests:**

1. The bridge works out whether the owner is away, tested with stand-ins for
   `loginctl` and the clock.
2. The bridge stores push subscriptions and sends Web Push (`pywebpush`).
3. The app gets a service worker, a "Notify me on this phone" button and a
   prompt view that opens on a tap (claude-voice-ui).
4. The voice app's instructions read prompts out (voice-integration).

## Consequences

- Most prompts should be answerable away from the laptop. How many is measured
  in the bridge's log, as the 5 of 66 was.
- **Worse:** while he is away, a session waits up to five minutes on a prompt he
  doesn't see in time, where today it would show in the terminal at once. It
  only matters to someone at the laptop while it counts him as away. Screen
  locked or ten minutes since the voice should make that rare. The five minutes
  can be made shorter.
- **Worse:** a new dependency (`pywebpush`, with `cryptography`) and a key pair
  to keep.
- **Worse:** Google's push service learns when the owner's phone gets a
  notification, though not what it says.
- A phone that is lost can still be told about prompts until its subscription is
  removed. Signing it out of the app removes the subscription.

## Related

`src/claude_voice/phone_hook.py`, `src/claude_voice/api.py` (prompts and
`HUB.listening`), ADR-CV-003 (risky actions wait for a spoken yes), the
README's "Answer questions and approvals from the phone".
