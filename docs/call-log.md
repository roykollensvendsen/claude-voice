# The call log

The bridge serves tools: the garage door, the sessions, the weather and the rest.
The owner wants to see how they are used: which ran, how long each took, and
which failed. The call log keeps that, and nothing of what was said.

## What a line holds

Each tool call writes one line when it ends, to
`~/.local/state/claude-voice/calls.jsonl`. Only the owner can read that file.
A line holds:

- `id`, `tool`, `at` and `ended_at` (UTC), and `ms`;
- `source`: `claude_app`, `voice_app` or `local`;
- `caller`: the calling session's name, if the call's `_meta` gives one. It is a
  claim, cut to 64 characters, shown and never trusted;
- `outcome`: `ok`, `error`, `refused` (the tool answered no), `timeout` or
  `cancelled` (the caller stopped waiting);
- `error_class`: `timeout`, `no_answer`, `refused`, `bridge_failed` or
  `caller_gone`, or null for ok.

A line never holds what was asked, the answer, or an error's message. A message
can carry a secret, so an error is kept as its class only.

Tools that clients call over and over to stay current, such as `whats_new`, are
counted in the totals but get no line.

## How long it is kept

- **The lines:** 90 days, and at most 20,000. The file is pruned every 500 lines,
  when it passes 4 MB, and at bridge start, by writing a new file and renaming it.
- **The daily totals:** two years, in the bridge's database. Each day and tool
  has its calls, its errors and a count per duration bucket. Only `error` and
  `timeout` count as errors.

Days are Oslo days. Today's totals are counted in memory and written at most 30
seconds apart, and when the bridge stops. The write runs on its own thread, so
no call waits on the database. After a crash, the bridge counts again the lines
that ended after the last write, on every day since. Only polling counts from
those seconds can be lost.

## What reads it

The health page reads the log through the bridge, signed in like the rest of
the page's API. A bad day, tool or limit gets a 400.

- `GET /api/v1/calls/totals?from=<day>&to=<day>`: per day and tool, the calls,
  the errors, and the median and 95th percentile in milliseconds. These are read
  from the duration buckets, so each is the top of a bucket; over a minute reads
  as 60,000. `unused` lists the tools offered with no call in that window. With
  no days given, it covers the last 14, and it covers at most 400.
- `GET /api/v1/calls/recent?limit=20`: the last calls, newest first, and the
  calls running now. The limit is 1 to 100.
- `GET /api/v1/calls/day/<day>/<tool>`: one day's lines for one tool. A polling
  tool says `counted_only`, since it has no lines.

Each call also sends two events on the page's live stream, `/api/v1/events`:
`call_start` with `id`, `tool`, `source`, `caller` and `at`, and `call_end` with
`id`, `tool`, `outcome`, `error_class`, `ms` and `ended_at`. Polling tools send
none.
