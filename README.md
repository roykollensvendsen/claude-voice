# claude-voice

Drive the Claude Code sessions on your own machine from a voice assistant on
your phone. You speak to ChatGPT Voice, it calls this bridge's MCP tools, and
the bridge runs Claude Code through the Claude Agent SDK, logged in with your
Claude subscription.

```text
ChatGPT Voice (phone)
   │  private plugin / MCP connector
   ▼
HTTPS tunnel (Cloudflare Tunnel, Tailscale Funnel, …)
   ▼
claude-voice  ── OAuth 2.1 (or static token), DNS-rebinding guard
   │  SessionManager + SQLite journal + ApprovalBroker
   ▼
Claude Agent SDK  ──►  Claude Code in ~/src/<project>
```

## What you can say

| Tool | For |
| --- | --- |
| `list_projects` | which projects exist under the root |
| `create_session`, `send_task` | start Claude on a task (returns at once) |
| `session_recap` | where one session stands, short enough to read aloud |
| `fleet_recap`, `recent_activity` | "what have my agents done since yesterday?" |
| `list_pending_approvals`, `approve`, `deny` | answer Claude's requests to edit, run commands, … |
| `cancel`, `close_session` | stop a turn, retire a session |
| `list_sessions`, `get_messages` | browse sessions and their full event log |
| `list_claude_conversations`, `attach_conversation` | pick up a conversation started in a terminal |

## How it behaves

- **One session = one Claude conversation.** Each prompt is a turn on a fresh
  SDK client that resumes the conversation by its Claude session id, so
  conversations survive restarts of the bridge. A session that was mid-turn
  when the bridge died is marked `interrupted`.
- **Your subscription, not the API.** Startup refuses to run while
  `ANTHROPIC_API_KEY` is set, because the SDK would then bill the API. Set
  `CLAUDE_VOICE_ALLOW_API_KEY=1` if that is what you want.
- **Your own Claude Code settings apply**: user, project and local settings,
  CLAUDE.md, and permission rules. Nothing is pre-approved beyond them.
- **Risky tool calls wait for a yes.** Whatever Claude Code would normally ask
  you about becomes a pending approval with a short id ("approval 3: Bash,
  `rm -rf build`"). Read-only tools (Read, Glob, Grep, …) pass. A request
  nobody answers is denied after ten minutes. Cancelling withdraws a session's
  requests.
- **Projects are confined** to `CLAUDE_VOICE_ROOT`. Paths outside it are refused.

> **Know the trust boundary.** The token proves a caller is *your* assistant;
> it cannot prove that *you* said yes. The tool descriptions tell the
> assistant to approve only on a clear spoken yes, but a misbehaving assistant
> could approve on its own. Keep your Claude Code permission rules sensible,
> and treat the token like an SSH key.

## Run

Requires Python 3.12+, [uv](https://docs.astral.sh/uv/), and Claude Code
logged in with your account (`claude` → `/login`).

```bash
uv sync
export CLAUDE_VOICE_ROOT=~/src

# stdio, for a local MCP client (Claude Desktop, MCP Inspector, …)
uv run claude-voice

# HTTP, for remote use
export CLAUDE_VOICE_TOKEN=$(python -c "import secrets; print(secrets.token_urlsafe(32))")
uv run claude-voice --transport http      # http://127.0.0.1:8811/mcp
```

| Variable | Default | |
| --- | --- | --- |
| `CLAUDE_VOICE_ROOT` | `~/src` | projects live directly under here |
| `CLAUDE_VOICE_TOKEN` | — | required for HTTP, at least 32 characters; also the OAuth login secret |
| `CLAUDE_VOICE_PUBLIC_URL` | `https://<first public host>` | OAuth issuer; the address clients see |
| `CLAUDE_VOICE_HOST` / `_PORT` | `127.0.0.1` / `8811` | keep it on loopback and tunnel in |
| `CLAUDE_VOICE_PUBLIC_HOSTS` | — | comma-separated hostnames the tunnel serves, e.g. `voice.example.com` |
| `CLAUDE_VOICE_DB` | `~/.local/state/claude-voice/bridge.db` | session journal |

`GET /healthz` answers without a token. `/mcp` accepts either:

- an **OAuth access token**, which is how ChatGPT connects; or
- the **static token** `CLAUDE_VOICE_TOKEN`, as `Authorization: Bearer …`, for local clients.

The OAuth flow:

- The client registers itself, then sends you to `/oauth/consent`.
- On that page you type `CLAUDE_VOICE_TOKEN` as the login secret. Each sign-in
  request allows five tries.
- Access tokens last an hour. Refresh tokens last 90 days and rotate on use.
- Tokens are stored only as hashes.

## Install as a service

```bash
deploy/install.sh     # systemd user service; token in ~/.config/claude-voice/env (mode 600)
journalctl --user -u claude-voice -f
```

## Reaching it from the phone

1. Make it reachable over public HTTPS. With Tailscale Funnel (enable Funnel
   for the machine in the Tailscale admin console first):

   ```bash
   tailscale funnel --bg --https=443 http://127.0.0.1:8811
   # -> https://<machine>.<tailnet>.ts.net/mcp
   ```

   Use port 443. claude.ai could not reach the bridge on port 10000, which
   ChatGPT could. `deploy/install.sh` already put `<machine>.<tailnet>.ts.net`
   in `CLAUDE_VOICE_PUBLIC_HOSTS`. Undo with `tailscale funnel --https=443 off`.
2. Register `https://<host>/mcp` as a private plugin in ChatGPT. On Plus that
   goes through Plugin Creator / ChatGPT Sites.

ChatGPT plugins authenticate with OAuth only, which the bridge provides. When
ChatGPT connects, it opens the consent page. Paste the login secret there once.

## Develop

The tests are written first. Each feature landed as a failing `test:` commit
followed by the `feat:` commit that makes it pass.

```bash
uv run pytest            # unit + MCP + HTTP tests against a scripted fake Claude
uv run pytest -m e2e     # against your real Claude Code; uses a little quota
uv run ruff check . && uv run ruff format --check .
```

`tests/fakes.py` replays scripted Agent SDK messages, built from the SDK's own
dataclasses, so almost everything is tested without Claude. CI
(`.github/workflows/ci.yml`) runs lint and tests on every pull request and push
to `main`. It never runs the e2e tests, so it needs no credentials.

## Origin

Rebuilt from the v0.1/v0.2 sketches in a ChatGPT Voice conversation on
2026-10-04, whose git bundle was lost when a phone call ended the session.
Changes from those sketches:

- `mcp` 2.x removed `FastMCP` in favour of `MCPServer`, so the old code no
  longer imported.
- Added an approval broker in place of the planned one.
- Added HTTP auth.
- Added project confinement.
- Added crash recovery.
- Added real tests.
