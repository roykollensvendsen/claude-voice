# claude-voice

Talk to the Claude Code sessions on your own computer from your phone.

You speak to a voice assistant (Claude's mobile app in voice mode is the one
this was proven with). The assistant calls this bridge over the internet,
and the bridge does the work on your computer:
- it starts and follows Claude Code sessions there;
- it passes your messages into sessions already open in a terminal;
- it reads back what they did.

Claude Code runs under your own Claude subscription, not a pay-per-use API key.
Anything risky, such as editing files or running commands, waits until you say
yes.

The bridge is an [MCP](https://modelcontextprotocol.io) server: a program
offering named tools that an AI assistant can call.

<!-- not run: a diagram, not commands -->
```text
voice assistant on the phone (Claude app, voice mode)
   │  custom connector, signed in with OAuth
   ▼
public HTTPS address (e.g. Tailscale Funnel)
   ▼
claude-voice on your computer
   ├─ sessions it runs itself ──► Claude Agent SDK ──► Claude Code
   └─ sessions open elsewhere ──► Claude Code's own session messaging and transcripts
```

## What you can ask for

| Tool | What it is for |
| --- | --- |
| `list_projects` | the folders Claude was used in lately, or any folder found by a word from its name |
| `create_session`, `send_task` | start Claude on a task; it answers at once and works in the background |
| `session_recap` | where one session stands, short enough to read aloud |
| `whats_new` | only what happened since you last asked: finished, failed, waiting for you |
| `fleet_recap` | every session in one call, each with a line on what it is doing |
| `health` | the bridge's own state: uptime, version, memory, calls and errors per tool, courier and watcher |
| `list_pending_approvals`, `approve`, `deny` | answer Claude's requests to edit files, run commands and so on |
| `cancel`, `close_session` | stop a turn, retire a session |
| `list_sessions` | the sessions this bridge started, and every one running on the computer |
| `session_tree` | the sessions as a tree, including which ones have been messaging each other |
| `list_active_sessions` | every Claude Code session running on the computer now, terminal or background |
| `message_active_session` | send a message into a session that is open in a terminal |
| `ask_active_session` | ask such a session something and wait for its answer, as if talking to it directly |
| `digest_session` | a long conversation summed up, or a question about it answered, in a few sentences |
| `read_session_output`, `search_session_history` | read what such a session said lately, or search everything it said |
| `list_claude_conversations`, `attach_conversation` | continue an earlier conversation, as a copy run by the bridge |

## How it behaves

- **One bridge session is one Claude conversation.**
  - Each prompt resumes that conversation, so it survives restarts of the bridge.
  - A session that was in the middle of a turn when the bridge stopped is
    marked `interrupted`.
- **Your subscription, not the API.** The bridge refuses to start while
  `ANTHROPIC_API_KEY` is set, because Claude would then be billed per token.
  Set `CLAUDE_VOICE_ALLOW_API_KEY=1` if that is what you want.
- **Your own Claude Code settings apply**, including your permission rules.
  Nothing is pre-approved beyond them.
- **Risky actions wait for a yes.**
  - Anything Claude Code would normally ask you about becomes a pending approval
    with a short number you can say aloud, such as "approval 3: Bash,
    `rm -rf build`".
  - Reading files needs no approval.
  - A request nobody answers is refused after ten minutes.
- **A session open in a terminal is never taken over.**
  - Messages are delivered into it, the way one Claude Code session messages
    another.
  - Continuing such a conversation from the bridge makes a separate copy, and
    the two then carry on independently.
  - The bridge never stops a terminal session.
- **Projects are confined** to `CLAUDE_VOICE_ROOT`. Folders outside it are
  refused.

> **Know the trust boundary.** Signing in proves that the caller is *your*
> assistant. It cannot prove that *you* said yes to a particular action. The
> assistant is told to approve only on a clear spoken yes, but a misbehaving one
> could approve on its own. Keep your Claude Code permission rules sensible, and
> guard the login secret like an SSH key.

## Run it

You need Python 3.12 or newer, [uv](https://docs.astral.sh/uv/), and Claude
Code signed in with your account (run `claude`, then `/login`).

`check` says whether the bridge could start, without starting it. It also asks
Claude Code whether it is signed in. When something is missing, it says how to
fix it, and when all is well it prints `ready to serve`. `check --json` lists
every check, for an assistant doing the setup:

```console
$ env -u ANTHROPIC_API_KEY -u CLAUDE_VOICE_ALLOW_API_KEY CLAUDE_VOICE_ROOT=no-such-folder claude-voice check
claude-voice: CLAUDE_VOICE_ROOT is not a directory: no-such-folder. Make it with `mkdir -p no-such-folder`, or set CLAUDE_VOICE_ROOT to the folder that holds your projects.
$ env -u CLAUDE_VOICE_ALLOW_API_KEY CLAUDE_VOICE_ROOT=src ANTHROPIC_API_KEY=sk-ant-example claude-voice check
claude-voice: ANTHROPIC_API_KEY is set, so Claude would bill the API instead of your subscription. Unset it, or set CLAUDE_VOICE_ALLOW_API_KEY=1 if that is intended.
```

`serve` runs it. Over HTTP it refuses to start without a login secret:

```console
$ env -u ANTHROPIC_API_KEY -u CLAUDE_VOICE_TOKEN CLAUDE_VOICE_ROOT=src claude-voice serve --transport http
claude-voice: CLAUDE_VOICE_TOKEN must be set to serve over HTTP. Make one with `export CLAUDE_VOICE_TOKEN=$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')`.
```

With a secret it listens on `http://127.0.0.1:8811/mcp`:

<!-- not run: it serves until stopped -->
```
export CLAUDE_VOICE_ROOT=~/src
export CLAUDE_VOICE_TOKEN=$(python -c "import secrets; print(secrets.token_urlsafe(32))")
uv run claude-voice serve --transport http
```

`serve --transport stdio` is for a local MCP client on the same computer. It is
also the default.

| Variable | Default | Meaning |
| --- | --- | --- |
| `CLAUDE_VOICE_ROOT` | `~/src` | Claude may only be started in folders under here |
| `CLAUDE_VOICE_TOKEN` | — | required over HTTP, at least 32 characters; it is also the login secret |
| `CLAUDE_VOICE_PUBLIC_URL` | `https://<first public host>` | the address clients reach the bridge at |
| `CLAUDE_VOICE_PUBLIC_HOSTS` | — | comma-separated hostnames the tunnel serves |
| `CLAUDE_VOICE_HOST` / `_PORT` | `127.0.0.1` / `8811` | keep it on this computer and tunnel in |
| `CLAUDE_VOICE_COURIER_NAME` | `Owner via claude-voice` | the sender name a session sees when the voice messages it, e.g. `Roy via stemmen` |
| `CLAUDE_VOICE_DB` | `~/.local/state/claude-voice/bridge.db` | where sessions and events are kept |

### Signing in

`GET /healthz` answers anyone. `/mcp` needs one of two things:
- an **OAuth access token**, which is how phone apps connect;
- the **login secret** itself, sent as `Authorization: Bearer …`, for local tools.

When an app connects, it registers itself and sends you to a sign-in page on
the bridge. You type the login secret there once.
- Each sign-in attempt allows five tries.
- Access tokens last an hour.
- Refresh tokens last 90 days and are replaced each time they are used.
- Tokens are stored only as hashes.

## Run it as a service and reach it from the phone

<!-- not run: installs a system service on the reader's computer -->
```
deploy/install.sh
journalctl --user -u claude-voice -f
```

The script installs a user service that starts with your login. It also writes
a fresh login secret to `~/.config/claude-voice/env`, readable only by you.

The phone needs a public HTTPS address. With Tailscale, first enable Funnel for
the machine in the admin console, then:

<!-- not run: changes the reader's Tailscale configuration -->
```
tailscale funnel --bg --https=443 http://127.0.0.1:8811
```

The bridge is then at `https://<machine>.<tailnet>.ts.net/mcp`. Use port 443:
claude.ai could not reach the bridge on port 10000.

In the Claude app, add a custom connector with that address and connect. Then
set the tools to *always allow*, so the app does not ask before each one.
ChatGPT on a Plus plan could not connect to a custom MCP server from Android at
the time of writing.

## Develop

[CONTRIBUTING.md](CONTRIBUTING.md) has the gates and how a change is made. In
short:
- Tests are written first, and run against a scripted stand-in for Claude, so
  they need no account.
- A separate set runs against a real signed-in Claude Code. It uses a little of
  the subscription and never runs in CI:

<!-- not run: needs a signed-in Claude Code and spends subscription quota -->
```
uv run pytest -m e2e
```

## Licence

GPL-3.0-or-later. See [LICENSE](LICENSE).
