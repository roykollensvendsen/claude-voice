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

## Start here: five minutes on one computer

You need Linux or macOS, a Claude subscription (Pro or Max), and an internet
connection. Nothing else has to be installed first.
An assistant doing this for you should read [AGENTS.md](AGENTS.md) first.

<!-- not run: installs software on the reader's computer; the clean-install job runs it in an empty container on every change -->
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh          # uv, which installs Python tools
curl -fsSL https://claude.ai/install.sh | bash           # Claude Code
export PATH="$HOME/.local/bin:$PATH"
uv tool install git+https://github.com/roykollensvendsen/claude-voice
mkdir -p ~/src                                           # the bridge only works in folders under here
claude-voice check
```

`check` now says that Claude Code is not signed in. Run `claude`, type
`/login`, sign in with your Claude account, and run `claude-voice check` again.
It should print `ready to serve over stdio`. Your projects belong in `~/src`;
to use another folder, set `CLAUDE_VOICE_ROOT` (see [the settings](#settings)).

Then connect it to Claude Code on the same computer:

<!-- not run: changes the reader's Claude Code configuration -->
```bash
claude mcp add --scope user claude-voice -- claude-voice serve
```

Start `claude` in any folder and ask: *"Which Claude Code sessions are running
on this computer?"* The answer comes from the bridge. In Claude Desktop, add
this to its configuration file instead (*Settings → Developer → Edit Config*),
with the full path that `which claude-voice` prints:

<!-- not run: a configuration snippet, not commands -->
```json
{ "mcpServers": { "claude-voice": { "command": "/home/you/.local/bin/claude-voice", "args": ["serve"] } } }
```

That is the whole bridge, used locally. The next two steps let you reach it from
a phone: [run it as a service](#run-it-as-a-service-and-reach-it-from-the-phone),
then give it a public address.

## What it depends on

Only a signed-in Claude Code is required. Each of the rest adds something, and
the bridge says which are missing, with the exact fix, through its
`check_dependencies` tool and the event API's `/api/v1/doctor`:

| Dependency | What it adds |
| --- | --- |
| Claude Code, signed in | everything; required |
| git | git status on the companion app's cards, and the contribute session |
| GitHub CLI (`gh`), signed in | pull requests on the cards, and the contribute session |
| uv | updating claude-voice and running its tests |
| Tailscale, with Funnel | reaching the bridge from the phone |
| a tailnet-only `tailscale serve` port | the companion app on the phone |
| the status line writing its figures | the plan's five-hour and weekly bars, and how full each session is |

## What you can ask for

| Tool | What it is for |
| --- | --- |
| `list_projects` | the folders Claude was used in lately, or any folder found by a word from its name |
| `create_session`, `send_task` | start Claude on a task; it answers at once and works in the background |
| `session_recap` | where one session stands, short enough to read aloud |
| `whats_new` | only what happened since you last asked: finished, failed, waiting for you |
| `fleet_recap` | every session in one call, each with a line on what it is doing |
| `get_active_session`, `set_active_session` | the session the owner talks to, chosen on the companion page or by voice |
| `check_dependencies` | which dependencies are missing for full use of the bridge, and the exact fix for each |
| `update_bridge` | update claude-voice: pull, install, test, restart, and roll back if anything fails |
| `update_changes` | what an update would bring: the new commits on GitHub, grouped by kind |
| `health` | the bridge's own state: uptime, version, memory, calls and errors per tool, courier and watcher |
| `list_pending_approvals`, `approve`, `deny` | answer Claude's requests to edit files, run commands and so on |
| `cancel`, `close_session` | stop a turn, retire a session |
| `list_sessions` | the sessions this bridge started, and every one running on the computer |
| `session_tree` | the sessions as a tree, including which ones have been messaging each other |
| `list_active_sessions` | every Claude Code session running on the computer now, terminal or background |
| `start_active_session` | start a new Claude Code session in the background, ready to talk to at once; with `create_folder`, a new project folder by a plain name under the root, only when asked |
| `start_role` | start a session for a role, such as `contribute`: say what to change in claude-voice and get a pull request |
| `stop_active_session` | stop such a session again; sessions the bridge did not start are never stopped |
| `message_active_session` | send a message into a session that is open in a terminal; answered `sending` when delivery outlasts the caller's time budget, and finished in the background |
| `ask_active_session` | ask such a session something and wait for its answer, as if talking to it directly |
| `side_question` | a quick answer about a busy session, read from what it wrote lately, never sent into it |
| `digest_session` | a long conversation summed up, or a question about it answered, in a few sentences |
| `refresh_session_summary` | a fresh summary of a session (the chosen one by default), shown on its page in the companion app too |
| `read_session_output`, `search_session_history` | read what such a session said lately, or search everything it said |
| `list_claude_conversations`, `attach_conversation` | continue an earlier conversation, as a copy run by the bridge |

## The companion app

[claude-voice-ui](https://github.com/roykollensvendsen/claude-voice-ui) is a
phone app, built as its own project, that sits beside the voice. It shows every session as a card. Tapping one makes it the session the
voice talks to, and while you talk it shows what that session last wrote and
whether it waits for you. It reads the bridge only through the
[event API](docs/event-api.md): a few JSON endpoints and one stream of live
events, behind the bridge's own login. Any other app can use the same API.

The app and the API answer only this computer and your tailnet, never the
internet through Funnel. To open it on the phone:

1. Clone and build the app (its README says how), and set `CLAUDE_VOICE_UI_DIR` in
   `~/.config/claude-voice/env` to its built folder; `deploy/install.sh` does
   this when it finds `../claude-voice-ui/dist`. Restart the service.
2. Give the bridge a port that only the tailnet reaches. Choose one or both:

<!-- not run: changes the reader's Tailscale configuration -->
```
tailscale serve --bg --https=8814 http://127.0.0.1:8811
tailscale serve --bg --tcp=8813 tcp://127.0.0.1:8811
```

   - `https://<machine>.<tailnet>.ts.net:8814/ui/` works when the phone can look
     up the computer's tailnet name.
   - `http://<the computer's tailnet address>:8813/ui/` works even when it
     cannot. It must be a TCP forward, because Tailscale's web ports answer
     only by name. The tailnet encrypts the traffic either way.
3. Name the address you will open in `CLAUDE_VOICE_UI_ORIGINS`, for example
   `http://100.x.y.z:8813`, so a change made in the app is accepted.

`--bg` keeps the ports across restarts. The bridge's `check_dependencies` tool
and `/api/v1/doctor` say whether such a port is there.

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

## Settings

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
| `CLAUDE_VOICE_UI_ORIGINS` | — | comma-separated origins of a companion app served elsewhere, allowed to call the [event API](docs/event-api.md) across sites |
| `CLAUDE_VOICE_UI_DIR` | — | a folder with a companion app's built files, served at `/ui/` |
| `CLAUDE_VOICE_DB` | `~/.local/state/claude-voice/bridge.db` | where sessions and events are kept |
| `CLAUDE_VOICE_ACCESS_TOKEN_SECONDS` | `3600` | how long a sign-in's access token lasts before the app must refresh it; set it to a week (`604800`) on a computer that sleeps, since the Claude app gives a connector up after about an hour of failed refreshes |

### Signing in

`GET /healthz` answers anyone. `/mcp` needs one of two things:
- an **OAuth access token**, which is how phone apps connect;
- the **login secret** itself, sent as `Authorization: Bearer …`, for local tools.

When an app connects, it registers itself and sends you to a sign-in page on
the bridge. You type the login secret there once.
- Each sign-in attempt allows five tries.
- Access tokens last an hour.
- Refresh tokens last 90 days and are replaced each time they are used. A replaced one
  still works for a minute, because an app may refresh twice at once.
- Tokens are stored only as hashes.

## Run it as a service and reach it from the phone

This step needs Linux with systemd; on macOS, run `claude-voice serve
--transport http` in a terminal for now. The script lives in the repository,
so clone it first:

<!-- not run: installs a system service on the reader's computer -->
```
git clone https://github.com/roykollensvendsen/claude-voice && cd claude-voice
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

## Answer questions and approvals from the phone

With the companion app open, a session's choice box or permission prompt can be
answered on the phone instead of at the terminal. A hook in your Claude Code
settings hands it to the bridge; with the app closed it steps aside and the box
shows in the terminal as before. Install it once, with the bridge's own Python:

<!-- not run: changes the reader's Claude Code settings -->
```
uv run python -m claude_voice.phone_hook --install
```

It merges two hooks into `~/.claude/settings.json`, keeps everything already
there, and saves the old file as `settings.json.bak`. The app and the API answer
only this computer and the tailnet, behind the login, and every answer is
logged. Anyone signed in to the app can approve commands on this computer, so
sign in only on your own devices.

## Where your clones are

The bridge works out where the project's repositories are, on GitHub and on
this computer, and the defaults suit a clone of this repository with the
companion app next to it. To change any of it, for example to work from a fork
or to keep the clones elsewhere, write `~/.config/claude-voice/config.toml`
(another file can be named in `CLAUDE_VOICE_CONFIG`):

<!-- not run: a configuration example, not commands -->
```toml
[repos]
claude_voice = "roykollensvendsen/claude-voice"        # the project, on GitHub
claude_voice_ui = "roykollensvendsen/claude-voice-ui"
fork = "you/claude-voice"                               # your fork, if you have one

[paths]
claude_voice = "~/src/claude-voice"          # default: the clone the bridge runs from
claude_voice_ui = "~/src/claude-voice-ui"    # default: next to it
ui_dist = "~/src/claude-voice-ui/dist"       # default: inside it; CLAUDE_VOICE_UI_DIR wins
contribute = "~/src/claude-voice"            # the contribute session's clone; default: claude_voice

[sessions]
remote_control = true                        # a started session can be opened in the Claude app
launch = "terminal"                          # or "background"
terminal_command = "kitty --directory {cwd}" # default: gnome-terminal, kgx, konsole or xterm, the first found

[budgets]                                    # seconds each kind of client is answered within
claude_app = 20
voice_app = 120
default = 60
```

A session the bridge starts opens in a terminal window on your desktop, so you
can watch it and type into it, and with Remote Control, so the Claude app on
your phone can open it too. Without a desktop it opens in tmux (`tmux attach`
reaches it), and with no terminal at all in the background. The companion
app's settings page changes `remote_control` and where sessions start, and one
start can ask otherwise; the terminal command is set only here.

Each kind of client is answered within its own time budget, set in a `[budgets]`
section: the Claude app drops a tool call after about 30 seconds, so it gets 20
(`claude_app`); the voice app waits longer and gets 120 (`voice_app`); anything
else gets `default`, 60. A message that takes Claude Code longer to deliver is
answered "sending" and delivered in the background, and a wait for a session's
answer ends at the budget with "still working".

The update works in `claude_voice`. The contribute session works in
`contribute`, and sends its pull request to `repos.claude_voice`; it may push
only to your `fork`. The doctor checks that every folder exists, is a clone,
and points at the right repository or your fork, and says how to fix it if not.

## Send a change back

Ask the bridge for a *contribute* session, by voice or in the companion app,
and say what you want changed. It works in your own clone, makes a fork under
your GitHub account if needed, runs the tests and opens a pull request here. It
cannot push to this repository itself. [CONTRIBUTING.md](CONTRIBUTING.md) has
the rules it follows, which apply to any change.

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

## Acknowledgements

- [kelno](https://github.com/kelno) measured that the Claude app drops a tool call
  after about 30 seconds, and capped waiting calls in their fork; the time budgets
  here come from that idea. Several earlier changes came from their fork too.
- [AndresSM415](https://github.com/AndresSM415) reported, with care and with fixes,
  that the sign-in could be guessed at without end and that strangers could
  register apps without limit (issues #75 and #76).

## Licence

GPL-3.0-or-later. See [LICENSE](LICENSE).
