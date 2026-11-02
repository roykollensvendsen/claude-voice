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

- `number`: the session's short number, for saying it aloud ("number 3"). A
  new session gets the smallest free one and keeps it while it lives; a number
  whose session ended is not given to another for a day. Every place that takes
  a session, in this API and in the voice tools, also takes its number as `3`,
  `nr 3`, `nummer 3` or `number 3`. It is on the `active` object and the
  `status` event too.
- `kind`: `terminal`, `background` or `bridge-session`.
- `state`: one of `working`, `idle`, `answer` (waiting for an answer to a
  question), `approval` (waiting for permission to use a tool), `input`
  (waiting for something else), `moved` or `stopped`; the same set for
  every kind of session. `state_since` is when it last changed, as the bridge
  saw it: exact to about five seconds, and from the bridge's start at the
  earliest. `waiting_kind` is `question`, `approval`, `input` or `null`.
- `status`: `idle`, `busy`, `shell` (Claude has finished its turn while a command it
  started runs on in the background; its `state` is `idle`), `waiting` (for its owner)
  or `moved` (its conversation continues in `moved_to`).
- `doing`: one line on what it is doing, fit to show or say. It starts
  `venter på valg:` when it waits on a question.
- `question` and `waiting_for` appear only while it waits.
- `app_url`: the address that opens the session in the Claude app, such as
  `https://claude.ai/code/session_…`, while it is connected to Remote Control;
  otherwise `null`. A terminal session gets one when it is started with
  `claude --remote-control`, or connected later with `/remote-control`.
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

## Usage

`GET /api/v1/usage` answers how much of the plan is used and an estimate of
voice activity through the bridge:

```json
{"plan": {"five_hour": {"used_percentage": 12, "resets_at": "<iso>", "stale": false} or null,
          "seven_day": {...} or null,
          "as_of": "<iso>", "label": "includes chat and voice; as of the last answer"} or null,
 "warning": {"status": "allowed_warning" or "rejected", "used_percentage", "at"} or null,
 "voice": {"label": "estimate, voice activity via the bridge",
           "today": {"connections", "minutes", "tool_calls", "chars_out", "est_tokens"},
           "last_7_days": [{"day": "2026-11-02", "connections", "minutes", "tool_calls", "chars_out", "est_tokens"}]}}
```

- **The plan** comes from Claude Code's status line, from the newest one that
  had the figures. A figure past its reset is `stale`. A plan may report only
  one of the two windows, so either can be `null`. The figures cover chat,
  voice and Claude Code alike, and move only when a session talks to the model.
- **`warning`** appears from 80 per cent of a live figure: `rejected` at 100.
- **Voice** counts the calls the bridge served to the Claude app (from
  elsewhere) and to the voice app (from this computer, with a trace id). Calls
  more than ten minutes apart are separate conversations. Tokens are characters
  divided by four. It is an estimate of activity through the bridge, not a
  measure of the plan.
- **No cost in money is ever given:** on a subscription it is notional.

Each session in the list also carries `context`: `{"used_percentage", "as_of"}`
from its own latest status line, or `null`.

The status line's input reaches only the status line script. The owner's script
therefore also writes it, one file per session, to
`~/.local/state/claude-voice/statusline/<session id>.json`. Add this after the
line that reads the input, in a script that has it in `$input`:

<!-- not run: it belongs in the reader's own status line script -->
```bash
cv_state="$HOME/.local/state/claude-voice/statusline"
cv_sid=$(echo "$input" | jq -r '.session_id // empty')
case "$cv_sid" in ""|*[!A-Za-z0-9-]*) ;; *)
  mkdir -p "$cv_state" && echo "$input" | jq -c '{session_id, at: now,
    context_window: (if .context_window then {used_percentage: .context_window.used_percentage} else null end),
    rate_limits: (.rate_limits // null)}' > "$cv_state/$cv_sid.json.tmp" && mv "$cv_state/$cv_sid.json.tmp" "$cv_state/$cv_sid.json";;
esac
```

## Updating the bridge

| Call | Answer |
| --- | --- |
| `GET /api/v1/bridge` | `{"version": "<short commit>", "git": {...}}`: the version running, and where the bridge's own folder stands, in the same `git` shape as a session's. `git.tracking.behind` above 0 means an update is waiting (as of the last fetch) |
| `POST /api/v1/update` | starts an update and answers `202 {"started": true, "note"}`; `409` while one runs, `404` when the bridge does not run from a git clone. A change: with the cookie it needs an allowed `Origin` |
| `GET /api/v1/update` | the latest update: `{"running", "started_at", "from", "to", "steps": [{"step", "ok", "detail", "at"}], "result", "detail"}` |

The update runs outside the bridge, because it restarts it. In order:
1. Local changes to tracked files stop it before anything is touched.
2. Up to date: nothing more happens.
3. Otherwise it pulls a fast-forward only, installs, and runs the tests.
4. If installing or the tests fail, the folder goes back to the version that
   ran, and the bridge is not restarted.
5. If they pass, it restarts the bridge and checks that it answers. If it does
   not, the old version is put back and started again.

`result` is `updated`, `up_to_date`, `stopped` or `rolled_back`. Each step also
arrives as an `update` event. The event stream drops during the restart, so
reconnect and read `GET /api/v1/update` for the rest.

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

## A session's own page

| Call | Answer |
| --- | --- |
| `GET /api/v1/sessions/{session}/detail` | `{"session", "number", "name", "context", "tool_calls": {"total", "by_tool": {"<tool>": n}}, "subagents": {"running", "total"}, "timeline": [{"at", "kind", "text", "url"?}], "summary", "summary_state", "summary_error", "summary_at"}` |
| `POST /api/v1/sessions/{session}/summary` | `{"language"?: "norsk"}`; `202` with `{"session", "state": "generating"}`, also when one is already being made (it joins that one); `404` for a session that is not running. A change: with the cookie it needs an allowed `Origin` |

`{session}` is a name, an id or a number. Everything on the page is read from the
session's transcript, so it costs no model call, and the page never makes a
summary: only the POST does, with a small model, and so does the voice tool
`refresh_session_summary`. `context` is the same object as on the cards.
`timeline` holds at most the 50 newest items, newest first, of the kinds
`prompt` (what the owner typed), `question`, `error` (a tool that failed),
`commit`, `pr_opened` (with `url`), `pr_merged` and `compact`. A helper
(`subagents`) runs until it has handed its result back. `summary_state` is
`none`, `generating`, `ready` or `error` (then `summary_error` says why, and the
last good `summary` is kept). The stream carries `summary` with
`{"session", "summary", "summary_state"}` when one is ready or has failed.

## How new sessions start

| Call | Answer |
| --- | --- |
| `GET /api/v1/settings` | `{"remote_control": true, "start_in": "terminal"\|"background", "updated_at": null\|"<time>"}` |
| `PUT /api/v1/settings` | any of `remote_control` (true or false) and `start_in`; returns the whole object. `400` with `{"error"}` for a key that does not exist or a value that does not fit. A change: with the cookie it needs an allowed `Origin` |

A session the bridge starts (`start_active_session`, a role) opens in a terminal
window on the desktop, or in tmux when no desktop is reachable, and with Remote
Control, so it can be opened in the Claude app; this page changes that for every
new session, and `start_active_session` takes `start_in` and `remote_control` to
change it for one start. Its reply says `started_in` (`window`, `tmux` or
`background`). The defaults, and which terminal opens, are the settings file's
`[sessions]` section; the terminal command is never set from here. The stream
carries `settings` with the whole object after each change.

## Answering from the phone

Questions (Claude Code's choice boxes) and permission prompts from any session
can be answered here while the app is open. A hook in the owner's Claude Code
settings hands each one to the bridge and waits for the phone; when no stream is
open it steps aside at once and the box shows in the terminal as before, and if
no answer comes within five minutes the box shows there too.

| Call | Answer |
| --- | --- |
| `GET /api/v1/prompts` | the open ones: `{"prompts": [{"id", "session", "kind": "question"|"approval", "question": {"text", "header", "options": [{"label", "description"}], "multi"}, "tool", "input", "cut", "asked_at"}]}`. `input` is what an approval would run: the command, the file, or short JSON, at most 300 characters |
| `POST /api/v1/prompts/{id}` | for a question `{"answer": "<label>"}`, `{"answer": ["<label>", …]}` when `multi`, or `{"answer": {"text": "…"}}`; for an approval `{"decision": "allow"|"deny", "reason"?}`. `200`, `409` when it does not fit the prompt, `404` when it is gone. A change: with the cookie it needs an allowed `Origin` |

Read `GET /api/v1/prompts` on every connect; the stream only carries changes:
`prompt` (one opened) and `prompt_closed` (`{"id", "how": "phone"|"timeout"|"screen"}`).
Every answer is logged with its session, the answer and who gave it.

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
| `focus` | `{"session": "<id or name>", "tool": "ask_active_session", "client", "at": "<iso>"}` | the voice, or any MCP client, just talked to a session: asked it, messaged it, gave it a task, stopped it |
| `peek` | the same fields | a caller only looked at a session: read it, searched it, summed it up, asked a side question about it. Not talking to it |
| `active` | the `active` object | the session the owner talks to changed |
| `status` | `{"session": "<id>", "name", "state", "previous", "since", "doing", "at"}` | a session's `state` changed |
| `git` | `{"session": "<id>", "git": {...}}` | a session's git status changed |
| `output` | `{"session": "<id>", "index", "role"}` | a running session wrote a new turn; read it with `/sessions/{id}/output?after=` |
| `update` | one step: `{"step", "ok", "detail", "at"}`, with `step` one of `check`, `fetch`, `pull`, `install`, `test`, `restart`, `verify`, `rollback`, `done` | an update made progress |
| `news` | one item: `{"kind", "session", "text", …}` | something happened worth telling: a session finished, failed, started waiting, asked for an approval |

A comment line (`: keep-alive`) comes every 15 seconds so proxies keep the
stream open. Reconnecting is safe; nothing is replayed.

`client` says who called: `claude_app` (from the internet, through the Claude
app's connector), `voice_app` (this computer, with a trace id), `local` (another
program here, such as Hermes) or `in_process`.

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
