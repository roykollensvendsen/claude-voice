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
     "question": {"text": "Which file?", "options": ["a.py", "b.py"]},
     "programme": {"id": "acme", "name": "Acme Robots"}, "component": {"id": "arm", "name": "Robot arm"},
     "role": null, "programme_wide": false, "address": "acme/arm"}
  ],
  "programmes": [{"id": "acme", "name": "Acme Robots", "order": 0}],
  "programme_hints": [],
  "active": {"id": "5f3c…", "name": "billing-ab", "kind": "terminal",
             "set_by": "page", "set_at": "2026-11-02T13:05:00+00:00"}
}
```

- `number`: the session's short number, for saying it aloud ("number 3"). A
  new session gets the smallest number no running session uses, and keeps it while
  it lives; a number is free again as soon as its session ends. Every place that takes
  a session, in this API and in the voice tools, also takes its number as `3`,
  `nr 3`, `nummer 3` or `number 3`. It is on the `active` object and the
  `status` event too.
- `kind`: `terminal`, `background` or `bridge-session`.
- `machine`: `null` for a session on this computer, or the name of the other
  machine it runs on (see [other machines](other-machines.md)). A session there has
  an `id` with the machine in front, `lukas-laptop/840ab631…`, which every
  endpoint below takes, as a slash or as `%2F`. Its `name` is the machine's own
  name for it, and its `number` is `null`. One whose questions can only be answered
  in the Claude app carries `answer_in: "claude_app"`.
  `PUT /api/v1/active` takes it too; the `active` object then has the prefixed
  `id`, `machine`, and a `name` with the machine in front, the name the voice
  reaches it by.
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
  `waiting on a choice:` when it waits on a question.
- `question` and `waiting_for` appear only while it waits. `question` is the one the
  session asked last and is waiting on now. A question it went past (it used another
  tool, was interrupted, or was told something new) is not shown, nor is one asked
  inside a subagent. While Claude Code says it waits on a permission prompt, the
  state is `approval` and there is no `question`.
- `sending`: messages still being handed to the session, `[{"id", "text" (at most 80
  characters), "at"}]`, empty when none. A message lands here when Claude Code takes
  longer to deliver it than the sender's time budget (see `message_sent` below).
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
- `programme`, `component` and `role`: where the session sits in the
  [org chart](org-chart.md), each `{"id", "name"}` or `null`. A session with no
  `programme` belongs to no programme and is shown under "Other". A
  programme-wide role, such as a manager, has `component` `null` and
  `programme_wide` `true`. `address` is `"acme/arm/quality"`, `"acme/manager"`
  or `"acme/arm"` for a worker, and `null` outside any programme. A session on
  another machine is placed by its name only, so it can hold a role but has no
  component. An older bridge sends none of these.
- `active` is `null` until something has chosen a session.
- `programmes`, next to `sessions`: every programme that has an org file, in the
  order to show them, `[{"id": "acme", "name": "Acme Robots", "order": 0}]`.
  The `programmes` event says when it changes.
- `programme_hints`, next to `sessions`: groups of sessions in no programme that
  seem to belong together, `[{"id": "h-…", "sessions": ["<id>", …], "reason":
  "messages" | "folder" | "prefix", "detail": "acme-" or a folder's name or null,
  "messages": <how many in the last 7 days> or null}]`. Empty when there are none.
  [The org chart](org-chart.md#hints-for-the-sessions-in-no-programme) gives the
  rules. The `programme_hints` event says when they change.

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

## Other machines

`GET /api/v1/sessions` also carries `machines`, one per approved machine:

```json
{"machines": [{"name": "lukas-laptop", "state": "online", "reachable": true, "error": null,
               "seen_as": "hjemme", "latency_ms": 54, "checked_at": "<iso>",
               "last_seen": "<iso>", "sessions": true}]}
```

- `state`: `online`, `slow` (answered in the last minute, not now), `offline`,
  `agent_down` (on, but nothing answers on the agent's port) or `not_approved`.
- `error`: `null`, `did not answer`, `not the machine you approved` or
  `other tools than approved`.

The hub asks every machine every ten seconds, all at once, for two seconds at most,
and answers the page from what it last heard: a machine away never delays it. A
machine that does not answer has its sessions left out. What changed between two
asks reaches the stream as `status` events with the prefixed `session` and
`machine`, and a `machine` event carrying the object above when its `state`
changes, so remote news is up to ten seconds late.

`GET /api/v1/prompts` includes the questions on other machines that can be
answered from here, with a prefixed `id` and `session` and a `machine`.
`POST /api/v1/prompts/{id}` passes the answer on, and answers `502 {"error":
"lukas-laptop did not answer"}` when it cannot. A question that goes away on the
machine itself closes with `prompt_closed` and `how: "screen"`.

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
| `GET /api/v1/bridge` | `{"version": "<short commit>", "git": {...}, "started_at": "<iso>"}`: the version running, when this bridge started, and where the bridge's own folder stands, in the same `git` shape as a session's. `git.tracking.behind` above 0 means an update is waiting (as of the last fetch) |
| `POST /api/v1/update` | starts an update and answers `202 {"started": true, "note"}`; `409` while one runs, `404` when the bridge does not run from a git clone. A change: with the cookie it needs an allowed `Origin` |
| `GET /api/v1/update` | the latest update: `{"running", "started_at", "from", "to", "steps": [{"step", "ok", "detail", "at"}], "result", "detail"}` |
| `GET /api/v1/update/changes` | what an update would bring: `{"current", "latest", "behind", "groups": [{"type": "feat"\|"fix"\|…\|"other", "title", "commits": [{"sha", "subject", "breaking"}]}], "fetched", "note"?, "checked_at"}`. The commits on GitHub this clone has not pulled, oldest first, grouped by the kind their subject names, new things first; no model is involved. It fetches from GitHub, so it takes a moment; `note` says when GitHub could not be reached |

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

## How the software is doing

| Call | Answer |
| --- | --- |
| `GET /api/v1/health/software` | `{"as_of", "repos": [{"name", "url", "as_of", "metrics": [{"id", "label", "value", "unit", "text", "status": "ok"\|"warn"\|"bad"\|"exempt"\|"unknown", "url", "trend": [≤30 values, oldest first]}]}]}` |

One entry for claude-voice, one for claude-voice-ui and one for each plugin's
own repository (the one its checked-out branch pushes to), read from the levels
main keeps with each run of its checks (see CONTRIBUTING, "The quality ratchet"),
from each clone's `quality.json` for the time before that, and from the latest
run of the checks on main; kept for ten minutes. The measures, by `id`:
`coverage`, `mutation` (rules proven by a test, in percent), `tests`, `problems`
(lint and type errors), `cycles`, `layers` (imports that break the layer order),
`duplication` (the share of lines in repeated blocks), `complexity` (the most tangled function),
`test_time` (against its budget) and `ci` (the checks on main, with the run as
`url`). The trend is each measured version, oldest first, so it moves change by
change. A repository with nothing measured has every measure `unknown` rather
than a guess.

A measure the owner has agreed to leave outside its limit is `exempt`, never
`ok`. The agreement is kept in the repository's own `quality.toml`, one entry
per measure, and counts only with both a written reason and the owner's yes:

<!-- not run: an example entry in a repository's quality.toml -->
```toml
[[exempt]]
measure = "complexity"
reason = "the tool table is one long match on the tool's name"
approved = "Roy, 2026-11-11, in pull request #212"
```

An exempt measure's `text` ends with the reason. A measure inside its limit
stays `ok` whatever the file says, and an entry missing its reason or its
approval is ignored.

## The team's work

| Call | Answer |
| --- | --- |
| `GET /api/v1/work` | `{"as_of", "waiting_on_owner": [{"id", "text", "command"\|null, "since", "repo"\|null, "session"\|null}], "open_prs": [{"repo", "number", "title", "url", "checks": "pass"\|"fail"\|"pending", "draft"}], "unreadable": ["<repo>"], "developer_mode": {"on", "until"\|null}}` |

What waits on the owner is the manager session's own list, read fresh on every
call from `~/.local/state/claude-voice/waiting.json`; an item with no `id` or
`text` is left out. The open pull requests are those of claude-voice,
claude-voice-ui and every plugin's repository, asked of GitHub at most once a
minute; a repository whose pull requests could not be read is named in
`unreadable` rather than shown as having none. `text` and `command` are plain
text, never markup. `developer_mode` says whether the owner's voice may approve a
plugin whose tools changed, and until when (ADR-CV-010); it ends by itself.

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
`none`, `generating`, `ready` or `error` (then `summary_error` says why and what to
do next: try again, and if it fails again run `claude-voice check`, which says what is
missing; the last good `summary` is kept). The stream carries `summary` with
`{"session", "summary", "summary_state"}` when one is ready or has failed.

## Opening a session's terminal

Decided in [ADR-CV-012](../decisions/ADR-CV-012-a-session-opens-where-the-owner-is.md).

| Call | Answer |
| --- | --- |
| `GET /api/v1/whoami` | `{"here": true \| false}`. Needs no signing in: it tells only whether the asker is on this computer |
| `POST /api/v1/sessions/{id}/focus` | no body. `200` `{"done": "raised"}` (a terminal holding it was brought to the front) or `{"done": "attached"}` (a new terminal was opened on its tmux session); `401` without signing in; `403` `{"reason": "not_here"}`; `409` `{"reason": "no_window" \| "not_running" \| "not_local"}`; `501` `{"reason": "no_window_list"}` |

A request is **here** only when all of these hold: it comes from a loopback
address; it has no `X-Forwarded-For`, `Forwarded` or `Tailscale-User-*` header;
its `Host` is `127.0.0.1` or `localhost` on the bridge's own port; and its
`Origin`, when it has one, is `http://127.0.0.1:<port>` or
`http://localhost:<port>`. So the page served at `/ui/` from `127.0.0.1:8811` is
here, and the same page reached through Tailscale, Funnel or a preview server is
not, though those connect from this computer too. The page reads `whoami` once
when it starts and shows "Open in terminal" only when it is here, and only for a
session whose `machine` is `null`.

`{id}` is the session's id, not its name or number. A session in tmux gets the
terminal already attached to it raised, or else a new `gnome-terminal` running
`tmux attach-session` on it (`attached`). A session in a plain terminal gets the
window that holds it raised; that window is the one terminal window whose title
is the session's name after Claude Code's status sign. Nothing ever starts
`claude` again. `no_window`: the session is not in a terminal (a background or
bridge session), or its window was not found or found twice, or the desktop would
not raise it. `not_running`: no running session has that id. `not_local`: the
session is on another machine. `no_window_list`: this desktop cannot list or
raise windows (the computer-use-linux GNOME Shell extension is not running) or has
no `gnome-terminal`.

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

Some prompts never reach the phone, and their box shows at once:

- a choice box that asks several questions at once, because the phone shows one and
  Claude Code drops any question left without an answer;
- a choice box in the session the voice is talking to, which another hook refuses;
- the permission Claude Code asks for the choice box itself, which would otherwise
  show as a second prompt beside the question.

When the bridge no longer knows a prompt the hook waits on, after a restart for
example, the box shows at once rather than after five minutes.

| Call | Answer |
| --- | --- |
| `GET /api/v1/prompts` | the open ones: `{"prompts": [{"id", "session", "kind": "question"|"approval", "question": {"text", "header", "options": [{"label", "description"}], "multi"}, "tool", "input", "cut", "asked_at"}]}`. `input` is what an approval would run: the command, the file, or short JSON, at most 300 characters |
| `POST /api/v1/prompts/{id}` | for a question `{"answer": "<label>"}`, `{"answer": ["<label>", …]}` when `multi`, or `{"answer": {"text": "…"}}`; for an approval `{"decision": "allow"|"deny", "reason"?}`. `200`, `409` when it does not fit the prompt, `404` when it is gone. A change: with the cookie it needs an allowed `Origin` |

Read `GET /api/v1/prompts` on every connect; the stream only carries changes:
`prompt` (one opened) and `prompt_closed` (`{"id", "how"}`).
Every answer is logged with its session, the answer and who gave it.

A prompt is listed only while the hook still waits on it, because an answer reaches
the session through that wait and no other way. It closes, with `how` saying why, when:

| `how` | What happened |
| --- | --- |
| `phone` | it was answered here |
| `timeout` | no answer came in time, and the box showed in the terminal |
| `screen` | nobody had the app open, so the hook stepped aside at once and the box showed in the terminal |
| `ended` | the hook stopped waiting and the session is no longer running |
| `moved_on` | the hook stopped waiting and the session is no longer waiting on anyone |
| `hook_gone` | the hook stopped waiting without saying so, for example when it was killed |

The bridge looks every five seconds. It closes a prompt once no hook has waited on it
for thirty seconds, so a killed hook's prompt is gone within about a minute and a half.

## Roles

A role is a background session started for a set purpose. The built-in one,
`contribute`, changes claude-voice in the owner's clone and opens a pull
request; it can never push to the project's own repository. A UI shows roles
as pinned cards above the sessions.

| Call | Answer |
| --- | --- |
| `GET /api/v1/roles` | `{"roles": [{"name", "title", "description", "session": {"name", "id", "status"} or null}]}`; `session` is the running session last started for the role |
| `POST /api/v1/roles/{name}/start` | with `{"task": "what to change"}` or `{}`: starts a session for the role and answers `{"role", "name", "claude_session_id", "status"}`. `404` for an unknown role, `502` when Claude Code could not start it. A change: with the cookie it needs an allowed `Origin` |

## The org chart

Which programme, component and role each session belongs to, read from each
programme's own file ([the org chart](org-chart.md)). These calls only read:
the chart changes in its file, never through the API. The page reads them when
it shows the chart; they are not streamed, but the `programmes` event says when
the list of programmes changed.

| Call | Answer |
| --- | --- |
| `GET /api/v1/org` | every programme, in order: `{"programmes": [{"id", "name", "order", "roles": <how many>, "unfilled": ["<role id>"]}]}` |
| `GET /api/v1/org/{programme}` | one programme by its `id`, as below; `404` for an unknown one |

```json
{
  "id": "acme", "name": "Acme Robots", "aliases": ["robots"],
  "components": [{"id": "arm", "name": "Robot arm", "aliases": ["the arm"]}],
  "roles": [
    {"id": "manager", "name": "Manager", "component": null, "programme_wide": true,
     "address": "acme/manager", "purpose": "Keeps the overview and brings the owner's decisions to the others.",
     "limits": "Never merges someone else's pull request without the owner.",
     "routing": ["decisions", "proposals"], "session_names": ["acme-robots"],
     "sessions": ["5f3c…"], "running": true},
    {"id": "quality", "name": "Quality", "component": {"id": "arm", "name": "Robot arm"},
     "programme_wide": false, "address": "acme/arm/quality", "purpose": "Watches the arm's tests and measurements.",
     "limits": "Changes tests, never the arm's code.", "routing": ["quality", "facts"],
     "session_names": ["arm-quality"], "sessions": [], "running": false}
  ],
  "unfilled": ["quality"]
}
```

- `routing`: what to send the role, from `facts`, `proposals`, `decisions` and
  `quality`.
- `session_names`: the names the file gives for the role. `sessions`: the ids of
  the sessions on this computer filling it now, the `id` the session list uses.
  `running` is whether there is one; a role in `unfilled` has none.

These are not the start roles above, which say how to start a new session.

## Live events

`GET /api/v1/events` is a stream of
[server-sent events](https://html.spec.whatwg.org/multipage/server-sent-events.html).
In a browser, `new EventSource("/api/v1/events")` with the cookie. A client
that must send a bearer token reads the stream with `fetch`.

| `event:` | `data:` (JSON) | When |
| --- | --- | --- |
| `focus` | `{"session": "<id or name>", "tool": "ask_active_session", "client", "at": "<iso>"}` | the voice, or any MCP client, just talked to a session: asked it, messaged it, gave it a task, stopped it |
| `peek` | the same fields | a caller only looked at a session: read it, searched it, summed it up, asked a side question about it. Not talking to it |
| `hello` | `{"started_at": "<iso>"}` | first, on every connect. A `started_at` other than the last one seen means the bridge restarted, not that the network dropped |
| `active` | the `active` object | the session the owner talks to changed |
| `message_sent` | `{"session": "<id>", "id", "delivered": true\|false, "detail"}` | a message that was answered `sending` has been handed over (or not), and left the session's `sending` list. Delivered means into Claude Code's inbox for that session, not yet read |
| `status` | `{"session": "<id>", "name", "state", "previous", "since", "doing", "at"}` | a session's `state` changed |
| `git` | `{"session": "<id>", "git": {...}}` | a session's git status changed |
| `output` | `{"session": "<id>", "index", "role"}` | a running session wrote a new turn; read it with `/sessions/{id}/output?after=` |
| `update` | one step: `{"step", "ok", "detail", "at"}`, with `step` one of `check`, `fetch`, `pull`, `install`, `test`, `restart`, `verify`, `rollback`, `done` | an update made progress |
| `programmes` | `{"programmes": [{"id", "name", "order"}]}` | an org file was added, removed or changed what it lists; looked at every ten seconds while a stream is open |
| `programme_hints` | `{"programme_hints": [...]}`, the list the session list carries | a hint was added, removed or changed; looked at every ten seconds while a stream is open |
| `work` | the `/api/v1/work` body | what waits on the owner, or an open pull request, changed; looked at every ten seconds while a stream is open |
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
