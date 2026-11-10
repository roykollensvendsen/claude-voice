# ADR-CV-012: A session opens where the owner is

## Status

Accepted 2026-11-10 by the owner.

## Context

The phone page lists what waits on the owner, and most items belong to a
Claude Code session. The owner wants to get to that session in one step from
the item. On the laptop that means its terminal. On the phone it means its page,
or the Claude app when the session can be opened there.

A page cannot open a terminal window. Only the bridge, a program on the laptop,
can. That makes this the first request a page can send that changes something
on the owner's screen, so it must only be possible from the laptop itself.

Checking that a request came from this computer is not enough. Tailscale and a
preview server both forward requests from the phone to the bridge, so those
arrive from this computer too.

## Decision

**What "here" means.** A request is from here only when all of these hold:

- it comes from this computer;
- it carries no forwarding header (`X-Forwarded-For`, `Forwarded`, or any
  `Tailscale-User-*` header);
- its `Host` is the bridge's own local address (`127.0.0.1` or `localhost`, on
  the bridge's port);
- its `Origin`, when there is one, is the bridge's own, so another website open
  in the owner's browser cannot send it.

`GET /api/v1/whoami` answers `{here: true|false}` by this rule. The page reads it
once when it starts.

**Opening a session.** `POST /api/v1/sessions/{id}/focus` uses the session's id,
not its name. It is refused with 403 `not_here` unless the request is from
here. A session on another machine gets 409 `not_local`.

For a session here:

- **In tmux:** a terminal already attached to it is raised. If there is none, a
  new terminal is opened, attached to it.
- **In a plain terminal:** the window that holds its terminal is raised.
- **Never:** a second copy of a session is never started.

On GNOME, raising a window goes through the Shell extension the desktop tools
already use, because a plain raise is ignored there.

Answers are codes, so the page can say them in its own languages:

| Code | Body |
| --- | --- |
| 200 | `{done: "raised" \| "attached"}` |
| 403 | `{reason: "not_here"}` |
| 409 | `{reason: "no_window" \| "not_running" \| "not_local"}` |
| 501 | `{reason: "no_window_list"}` |

**What the page shows.**

- Tapping an item still opens the session's page, as it does today.
- An "Open in terminal" button appears only when the page is here, and only for
  a session on this computer.
- On the phone, a session's page has an "Open in Claude app" link when that
  session can be opened there.

## Consequences

- The owner reaches a session in one tap: a terminal on the laptop, and its page
  or the Claude app on the phone.
- The bridge can now change what is on the owner's screen. The rule for "here",
  with a test for each part, is what keeps that from being reached through
  Tailscale or another website. A forwarded request that arrives from this
  computer is refused, and that has its own test.
- A session in a plain terminal whose window cannot be found is not opened
  again. The page says it could not find the window.
