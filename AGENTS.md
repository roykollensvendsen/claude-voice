# For an assistant setting this up for someone

You are helping a person install claude-voice, a bridge that lets a voice
assistant drive the Claude Code sessions on their computer. The steps
themselves are in the README's "Start here" section; follow them there rather
than from memory, because continuous integration proves that section on an
empty machine and nothing proves this page.

## Let the check lead

`claude-voice check --json` lists every check with `ok`, `required`, `detail`
and `fix`. Run it after each step, do the `fix` of the first required check that
fails, and stop when `ready` is `true`. Add `--transport http` once the person
wants to reach the bridge from a phone.

## What only the person can do

- **Sign in to Claude.** `/login` inside `claude` opens a browser for their own
  account. Ask them to do it; never sign in for them.
- **Approve a public address.** Exposing the bridge to the internet (for
  example `tailscale funnel`) changes their network. Ask first.
- **Answer approvals.** A pending approval is the person's yes or no. Never
  approve one yourself.

## What never to do

- **Never print the login secret** (`CLAUDE_VOICE_TOKEN`, kept in
  `~/.config/claude-voice/env`). It signs in anyone who has it. If the person
  needs it on another device, put it on their clipboard or tell them where the
  file is.
- **Never set `ANTHROPIC_API_KEY`** to get past a sign-in problem. The bridge
  refuses it on purpose: the person's subscription pays, not per-token billing.
  `CLAUDE_VOICE_ALLOW_API_KEY=1` is for a person who has chosen that, not for
  you.

## When the person wants another machine reached

The steps are in [docs/other-machines.md](docs/other-machines.md). On the other
machine, `claude-voice agent install --sessions` checks what is missing and says
what to add on the hub. Then:

- **Pairing is the person's.** They type the code the machine shows, and then its
  name, in `claude-voice machines approve <name>` on the hub. Never type either
  for them, and never pipe the code into it. A changed tool list makes the hub
  refuse the machine until they approve it again; that is the check working, not
  a fault to work around.
- **Never widen what an agent offers** to get something done: it offers its
  session tools and its own approved plugins, nothing about the bridge.
- **Never copy a key between machines.** The hub's key for a machine is in
  `~/.config/claude-voice/machines/<name>/key`; do not print it, move it or put it
  in a message.
- **Ask before installing** the agent on a machine, before `sudo` of any kind
  there, and before keeping a shell login on it once the agent is set up.

## When something fails

- **`check` is not ready:** do its `fix`.
- **The service will not stay up:** read `journalctl --user -u claude-voice -n 50`.
- **Something went wrong earlier**, such as the phone's connector showing as
  disconnected: `~/.local/state/claude-voice/bridge.log`, next to the bridge's
  database, keeps every request, each token refresh with its outcome, and each
  tool call, across restarts.
- **A phone cannot connect:**
  - `curl https://<their host>/healthz` should print `ok` from outside;
  - port 443 is the one claude.ai reaches.
- **The bridge runs but a tool misbehaves:**
  - its `health` tool lists the latest errors;
  - each error carries the trace id of the call that caused it.

## When you are changing claude-voice itself

If you are the session working in this repository on the owner's own machine,
you are the manager for the bridge and every plugin: see
[docs/organisation.md](docs/organisation.md) and the `manage` skill.

If you are a session working on this repository, rather than installing it:

- Follow [CONTRIBUTING.md](CONTRIBUTING.md): the gates, a failing test before
  the code, the mutation run, and the three rules for every change.
- Keep the quality ratchet: `uv run python scripts/ratchet.py check --base origin/main`
  must hold. Never make a measure worse without a `Ratchet-Exception:` line in
  the pull request saying why. Never edit `quality.json` to pass: main keeps the
  levels, and the file holds only the time budget.
- Push only to your owner's fork; a pull request goes to
  `roykollensvendsen/claude-voice` with `gh pr create --repo`.
- Never rename or remove a tool or field that Hermes or the voice app uses.
- When an outside contributor's pull request lands, thank them in it and say
  concretely what was good. Hold your own pull requests to what contributions
  have taught (CONTRIBUTING, "When someone else's change comes in").
- Never put a `Claude-Session:` line or a claude.ai/code session address in a
  commit message; the checks refuse it. Credit with `Co-Authored-By:` only.
