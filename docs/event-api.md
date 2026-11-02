# The event API

A companion app, such as the phone app claude-voice-ui, reads the bridge through
this API and nothing else. It is version 1 of the bridge's HTTP API for such
apps.

All paths are under the bridge's address, for example
`https://<machine>.<tailnet>.ts.net`, and start with `/api/v1`.

## Who can reach it

Only this computer and devices on the owner's tailnet. The bridge refuses
`/api/` and `/ui/` to every other address with `403`, so the API cannot be
reached through Tailscale Funnel even though the MCP endpoint is. Behind
Tailscale the address that counts is the last one in `X-Forwarded-For`, the one
Tailscale adds; a caller cannot get past by writing a tailnet address there
itself. A UI reaches the bridge through a tailnet-only `tailscale serve` port,
over HTTPS by name or over plain HTTP at the computer's tailnet address. The
tailnet encrypts both.

## Signing in

Every call needs one of these:

- **A bearer token**: `Authorization: Bearer <token>`, where the token is the
  bridge's login secret or an OAuth access token the bridge issued. A native app
  or a script uses this.
- **A sign-in cookie**: a page served by the bridge itself (see *Serving the
  UI*) signs in once and is then remembered for 30 days.

| Call | Body | Answer |
| --- | --- | --- |
| `POST /api/v1/signin` | `{"secret": "<login secret>"}` | `204` and a cookie `cv_ui`. `401` for a wrong secret, `429` after five wrong tries in ten minutes |
| `POST /api/v1/signout` | — | `204`; the cookie stops working |

The cookie cannot be read by scripts (`HttpOnly`) and is only sent to the same
site (`SameSite=Strict`). It is marked `Secure` when the request came in over
HTTPS, and not on a plain HTTP tailnet address, where a browser would drop it. A change made with the cookie (`signout`,
`PUT /active`) must carry an `Origin` header naming the bridge or an allowed UI
origin, or it is refused with `403`. Bearer calls need no `Origin`.

Without either, every call answers `401 {"error": "sign in first"}`.

## Sessions

`GET /api/v1/sessions`

Every Claude Code session running on the computer, and every session the bridge
ran in the last day, with the session the owner talks to:

```json
{
  "sessions": [
    {"id": "5f3c…", "name": "billing-ab", "kind": "terminal", "project": "billing",
     "status": "idle", "doing": "Fixed the export totals.", "minutes_since_update": 2,
     "question": {"text": "Which file?", "options": ["a.py", "b.py"]}}
  ],
  "active": {"id": "5f3c…", "name": "billing-ab", "kind": "terminal",
             "set_by": "page", "set_at": "2026-11-02T13:05:00+00:00"}
}
```

- `kind`: `terminal`, `background` or `bridge-session`.
- `state`: one of `working`, `idle`, `answer` (waiting for an answer to a
  question), `approval` (waiting for permission to use a tool), `input`
  (waiting for something else), `shell`, `moved` or `stopped`; the same set for
  every kind of session. `state_since` is when it last changed, as the bridge
  saw it: exact to about five seconds, and from the bridge's start at the
  earliest. `waiting_kind` is `question`, `approval`, `input` or `null`.
- `status`: `idle`, `busy`, `shell` (running a command), `waiting` (for its owner)
  or `moved` (its conversation continues in `moved_to`).
- `doing`: one line on what it is doing, fit to show or say. It starts
  `venter på valg:` when it waits on a question.
- `question` and `waiting_for` appear only while it waits.
- `git`: where the session's folder stands in git, or `null` when it is not a
  repository:
  `{"branch": "feat/x" or null when detached, "commit": {"hash", "subject"},
  "dirty", "changed_files", "tracking", "main", "parent", "checked_at"}`.
  `tracking`, `main` and `parent` are each `{"name", "ahead", "behind"}` or
  `null`. They compare against the branch it tracks, the remote's main branch,
  and for a fork the `upstream` remote's main branch. Only local refs are read
  and nothing is fetched, so `behind` is as fresh as the folder's last fetch.
  The answer is kept for 45 seconds per folder.
- `active` is `null` until something has chosen a session.

## The session the owner talks to

`GET /api/v1/active` answers the `active` object above, or `null`.

`PUT /api/v1/active` with `{"session": "<id or name>"}` makes that session the
one the voice sends to by default. It answers the new `active` object, or `404`
for a session that is not in the list. `DELETE /api/v1/active` clears the choice
(`204`), so the voice decides again; the `active` event then carries `null`. `set_by` is `page` for this call and
`voice` when the voice app changed it.

## One session

| Call | Answer |
| --- | --- |
| `GET /api/v1/sessions/{id}/output?limit=6&after=N` | its latest turns: `{"status", "turns": [{"index", "role", "text", "cut", "length"}], "next_after", "turns_left"}`. With `after`, only turns past that index; read on while `turns_left` is above 0 |
| `GET /api/v1/sessions/{id}/pending` | what it waits for: `{"waiting": bool, "question": {...} or null, "approvals": [{"id", "number", "tool", "summary"}]}` |
| `GET /api/v1/sessions/{id}/prs` | open pull requests of its project: `{"prs": [{"number", "title", "url", "draft"}]}`; empty when the folder is not on GitHub. Cached for a minute |

`{id}` is a session's `id` or `name` from the list. An unknown one answers `404`.
Turn texts are cut to 600 characters; `cut` and `length` say when.

## What is missing

`GET /api/v1/doctor` checks the bridge's dependencies and answers:

```json
{"ready": true, "checked_at": "<iso>",
 "checks": [{"id": "gh", "title": "GitHub CLI", "ok": false, "required": false,
             "affects": "pull requests on the cards, and the contribute session",
             "detail": "gh is installed but not signed in",
             "fix": "gh auth login", "fix_url": "https://cli.github.com/manual/gh_auth_login"}]}
```

- `ready` is false only when a required check fails; today that is only
  Claude Code's sign-in.
- `id`, `title`, `ok`, `required`, `detail` and `fix` are stable. `fix` is text
  for a person to run or follow, and never holds a secret.
- The ids are `claude_signed_in`, `git`, `gh`, `uv`, `tailscale`,
  `tailnet_port`, `statusline`, `role_clone` and `role_trust`.

The checks take a few seconds, so a UI asks on sign-in and then now and then,
not on every refresh.

## Roles

A role is a background session started for a set purpose. The built-in one,
`contribute`, changes claude-voice in the owner's clone and opens a pull
request; it can never push to the project's own repository. A UI shows roles
as pinned cards above the sessions.

| Call | Answer |
| --- | --- |
| `GET /api/v1/roles` | `{"roles": [{"name", "title", "description", "session": {"name", "id", "status"} or null}]}`; `session` is the running session last started for the role |
| `POST /api/v1/roles/{name}/start` | with `{"task": "what to change"}` or `{}`: starts a session for the role and answers `{"role", "name", "claude_session_id", "status"}`. `404` for an unknown role, `502` when Claude Code could not start it. A change: with the cookie it needs an allowed `Origin` |

## Live events

`GET /api/v1/events` is a stream of
[server-sent events](https://html.spec.whatwg.org/multipage/server-sent-events.html).
In a browser, `new EventSource("/api/v1/events")` with the cookie. A client
that must send a bearer token reads the stream with `fetch`.

| `event:` | `data:` (JSON) | When |
| --- | --- | --- |
| `focus` | `{"session": "<id or name>", "tool": "ask_active_session", "at": "<iso>"}` | the voice, or any MCP client, just called a tool aimed at a session: asked it, messaged it, read it, summed it up |
| `active` | the `active` object | the session the owner talks to changed |
| `status` | `{"session": "<id>", "name", "state", "previous", "since", "doing", "at"}` | a session's `state` changed |
| `git` | `{"session": "<id>", "git": {...}}` | a session's git status changed |
| `news` | one item: `{"kind", "session", "text", …}` | something happened worth telling: a session finished, failed, started waiting, asked for an approval |

A comment line (`: keep-alive`) comes every 15 seconds so proxies keep the
stream open. Reconnecting is safe; nothing is replayed.

`focus.session` is the value the caller passed, a name or an id, so match it
against both. A UI that follows the conversation switches its view to that
session on `focus`, and back to `active` when nothing has happened for a while.

## Serving the UI

The bridge can serve this UI's built files itself, so the page and the API share
one address and the cookie works with no extra setup:

- set `CLAUDE_VOICE_UI_DIR` to the folder with the built files;
- the bridge serves them at `/ui/`;
- the files are public; everything they show comes from the API, which is not.

A UI served from anywhere else must be named in `CLAUDE_VOICE_UI_ORIGINS`
(comma-separated origins, such as `https://ui.example`). The bridge then
answers that origin's cross-origin requests, cookie included.
