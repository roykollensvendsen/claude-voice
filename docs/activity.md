# The activity view

Which sessions wait, use many tokens, or are stuck inside one command. The
bridge reads what Claude Code already writes: each session's transcript and its
processes in `/proc`. It needs no hook and touches no session's settings.

## Off by default

The view is off until the owner switches it on, for 1 hour, 8 hours (the
default), 24 hours or 7 days. It turns itself off when that time ends. While it
is off, the bridge reads no transcript and no `/proc`. Once a day it deletes
counts older than seven days, which opens only the view's own file.

While it is on, the bridge looks every ten seconds. It reads only the bytes each
transcript gained since the last look. A transcript that shrank, or was replaced,
is read again from the start.

## Which programmes are read

Only the programmes named in the settings file are read. A session in no
programme of the org chart is never read. With no `[activity]` table, nothing is.

<!-- not run: an example settings table, with made-up programme codes -->
```toml
[activity]
orgs = ["voice", "quest"]   # the programmes the view reads
phone_orgs = ["voice"]      # of those, the ones served beyond this computer
```

A programme in `orgs` but not in `phone_orgs` is served only to a page opened on
this computer itself: a request with no forwarding header and no Tailscale user
header ([ADR-CV-012](../decisions/ADR-CV-012-a-session-opens-where-the-owner-is.md)). The phone,
through Tailscale, gets none of its rows, and a scope of it answers 404, as an
unknown one does. A programme in `phone_orgs` must also be in `orgs`, or the
bridge refuses the file. When a programme leaves `orgs`, its rows are deleted at
the next bridge start.

## What is kept

Numbers and kinds only, never what was said, asked or answered:

- **Per minute and session**, in `~/.local/state/claude-voice/activity.db`, which
  only the owner can read, for seven days: tokens read (`tok_in`: input, cache
  creation and cache read) and written (`tok_out`); tool calls by kind
  (`n_read`, `n_write`, `n_run`, `n_mcp`, `n_agent`); `tool_ms`, the time spent
  inside tool calls that ended that minute; `cpu_ms`, the CPU the session and
  every process under it used; `rss_mb_max`; and the seconds busy, idle and
  waiting. Also the stretches of time the view was on, so a gap is drawn as "not
  recording", never as zero. SQLite gives its passing journal file the database
  file's own permissions, so that too is the owner's alone.
- **In memory only**: each session's running tool call, its kind, the tool's name
  and, for a command, the program it runs, cut to a fixed list (git, gh, python,
  pytest, uv, make, cargo, npm, docker; anything else is "other"). The memory is
  empty after a bridge restart.

A tool call lasts from its use to its result in the transcript. One message's
tokens are counted once, however many lines repeat them. Lines written before
the view was switched on are not counted.

A call is **yellow** after 5 minutes. It is **red** after 10 minutes when the
session's CPU, children included, has not grown for 2 minutes. A command's
output reaches the transcript only when it ends, so the CPU is what tells a
long test run from a hung one.

Where the kernel keeps no list of each process's children, the bridge counts only
the session's own process, and `child` is `null`. Finding the children there would
mean reading every process on the computer, including those of programmes that are
not read. So a red there can also mean a child that still works.

## The endpoints

Every one needs the sign-in. A change needs the owner's sign-in, as
`PUT /api/v1/settings` does, and is refused with 401 or 403 and `{"error"}`.

| Call | Answer |
| --- | --- |
| `GET /api/v1/activity` | `{"on", "until": null\|"<time>", "durations": ["1h", "8h", "24h", "7d"], "scopes": {"orgs": [...], "sessions": [{"name", "org", "role"}]}}` |
| `POST /api/v1/activity` | `{"on": true, "for": "8h"}` or `{"on": false}`; answers as `GET`. `400` for a body that does not fit |
| `POST /api/v1/activity/clear` | empties the file: `{"cleared": true}` |
| `GET /api/v1/activity/series?scope=all\|org:<org>\|session:<name>&window=1h\|6h\|24h\|7d` | `{"scope", "window", "bucket_s", "from", "to", "recording": [{"from", "to"}], "points": [{"t", <the counts above>}]}`; `400` for another window, `404` for a scope not shown |
| `GET /api/v1/activity/now?scope=...` | `{"at", "sessions": [{"name", "org", "role", "status", "status_since", "call": null\|{"kind", "tool", "program", "started"}, "turn_started", "state": "ok"\|"yellow"\|"red", "cpu_pct", "rss_mb", "child": null\|{"program", "age_s"}}]}`; `404` while off |

The bucket is 60, 300, 900 or 3600 seconds for the four windows. A point is a
bucket that has a row; a bucket inside a `recording` stretch without a point is
zero. For a programme or all, a point is the sum over its sessions, and
`rss_mb_max` the sum of each session's own maximum. Times are this computer's
local time, with its offset.
