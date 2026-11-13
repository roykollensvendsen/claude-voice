---
name: write-plugin
description: Write or wire up a claude-voice plugin, an MCP server on this computer whose chosen tools the voice may call through the bridge. Use when someone wants a local tool (a device, a service, a script) reachable by voice, or a plugin is added, approved or changed.
---

# Writing a claude-voice plugin

A plugin is an ordinary MCP server over stdio. The bridge starts it as a process
of its own, offers the voice only the tools the owner approved, passes on the
plugin's own instructions, and leaves every safety rule to the plugin. The
README's "Plugins" section is the reference for the settings; a worked example
and the lessons from building this are in [reference.md](reference.md).

What every plugin must do once built, and which plugin does each thing best, is
[the plugin standard](../../../docs/plugin-standard.md).

## The checklist for a new plugin

The owner approved this list. Each item is a lesson a plugin had to learn late.

1. **A yes to a design is acted on at once.** Start the plugin's session and
   record it and its owner session in the programme's org chart
   (docs/org-chart.md). Expect its first status message within 5 minutes; a
   session that is silent may be stuck on something nobody sees.
   Anything blocking it, such as a folder-trust dialog or an approval, goes to
   the owner at once. Trusting a new folder is one of the owner's setup commands.
2. **Tool descriptions are all the voice sees.** Write each one, and run the cut
   test (standard A.2), from the very first tool, not at the end. Field
   descriptions name every input form accepted, such as a device name as well
   as a port, or the voice will only ever try one.
3. **Quality and a basic security review before the first approval.** The
   [`code-quality`](../code-quality/SKILL.md) skill's limits and `quality.toml` go in on day one, so the
   code-health page shows the plugin at once. The review covers any web or HTTP
   surface too, such as a route that acts without a login or an origin check.
4. **Fast start and safe restart before the voice uses it.** A test proves a
   cold first start (fresh environment, heavy imports) lists the tools within
   the bridge's start limit, or the voice sees nothing. A restart lets a running
   call finish.
5. **Personal setup lives in local config from day one.** PINs, addresses,
   device names and logins are collected with the `secret-box` skill, and never
   appear in the repository, logs or docs (standard A.10). Examples use made-up
   values, because history is hard to clean.
6. **The plugin's folder is the running code.** The bridge runs the plugin from
   that folder, so every change is made in a worktree, and the folder stays on
   its main (or voice) branch.
7. **Every change is read back** (standard A.5). After a command, read the
   device again, and say "not confirmed" if it does not show the change.
8. **Night and presence rules are written at design time, per device**, such
   as never switching a screen on at night, or never arming an alarm by voice
   (standard A.7, A.8). Added later, they get forgotten for one tool.
9. **Upstream work is public.** A pull request's text on a public fork is read
   by strangers. A security finding in someone else's code is held, unpushed,
   until the owner decides how to report it.
10. **Every error says what is wrong and what to do next.** A refusal or failure names
    the cause and the owner's next step, with the exact command when there is one: "The
    SSH key is missing on the MiSTer; run ssh-copy-id again." A bare "not answering" with no
    next step is a bug. This also applies when a quality session goes over an existing plugin.
11. **A quality round once v1 works.** A separate quality session or helper takes the
    plugin through the `code-quality` skill. It must be a fresh reader, not the session that
    built it, as tv, dreame and garage had. Its findings land before the plugin counts as done.

## The plugin

1. A few tools, each safe to call by voice. What cannot be undone asks first and
   acts only on a confirmation token after the owner's own spoken yes.
2. Every tool named after the plugin (`garage_status`).
3. The rules inside the plugin; the bridge's allow-list is a second lock.
4. Its own secrets read from a file it is pointed at, never an environment
   variable the bridge would hold.
5. Starts without a terminal: no questions, only MCP on stdout.
6. A simulated mode, for tests and the first try.

   A fake for a device or service copies its documented limits: rate limits,
   timeouts, attempt caps, expiry, and the error each one returns. Each limit has
   one test that breaks it and checks what the plugin does: waits, retries, or
   says why. The limit is one constant that both the client and the fake use, so
   they cannot drift. A limit that isn't documented is measured once against the
   real device, with the date written next to it.

   To show the old code fails, run it from its own checkout: `git worktree add <dir> main`,
   then run the tests with `PYTHONPATH=<dir>/src`. Print the module's `__file__` in that
   run, so the output shows which copy was tested. An editable install in a shared
   environment loads the branch, and the old code then 'passes'.

   A fake knows only the limits someone wrote down. The backstop is checklist
   rule 7 (every change is read back), plus one live try on the real device
   before a tool counts as done.
7. Its instructions say when to ask the owner and which tool takes which token.

## Wiring it in

1. `[plugins.<name>]` in `~/.config/claude-voice/config.toml`, with `branch` if
   its checkout follows a branch other than `main`.
2. The owner runs `claude-voice plugins approve <name>` and types the name. Hand it over as a
   command for an ordinary terminal, one per plugin, never as a `!` line in Claude Code. The
   `!` mode takes no typed answer. Say what to type when it asks: the plugin's name.
3. A plugin newly in the settings file needs one bridge restart to be read; after
   that, the running bridge picks up each approval by itself. `health` shows the
   plugin `running` after its first call.

After that, the plugin's own session never asks the owner to type again for new
code: merged code with the same tools runs after `plugin_restart`. A tool that is
added, removed, takes new inputs or changes its read-only mark is approved by the
owner's voice in developer mode (ADR-CV-010), once the change is merged and the
plugin's checkout is clean and level with its branch. Only a new command or folder
is typed again.
4. Simulated mode first, then the real thing, after the plugin's own session
   agrees.

## Bringing a new plugin into the team

The manager (see [docs/organisation.md](../../../docs/organisation.md)) does
these, in order, so a new plugin starts at the level the others reached:

1. **A repository of its own**, written by the `start-repo` skill's scaffold, so
   its checks, licence and contributing guide are there from the first commit.
2. **Locked like the others** once its checks have run once:
   `python ~/.claude/skills/start-repo/scripts/forge.py <owner>/<name>`. A pull
   request is then the only way onto its main, and nothing can force a push.
   A plugin that lives on a branch of a fork gets that branch locked the same way.
3. **A session of its own**, started in its folder and told the rules in
   docs/organisation.md: it asks the owner nothing directly, it merges its own
   green pull requests, and it reports to the manager.
4. **Its gaps against [the plugin standard](../../../docs/plugin-standard.md)**,
   sent to that session as a numbered list, each to be closed by its own pull
   request, with what to copy from which plugin.
5. **Wired in** as in "Wiring it in" above. The owner's approval is one command
   the manager gives them.
6. **Checked by voice**: after the restart, one of its tools is called through
   the bridge and its first lines are what the voice sees.

Never approve a plugin for the owner, and never put a tool on the allow-list
that the plugin's own session has not called safe for voice.
