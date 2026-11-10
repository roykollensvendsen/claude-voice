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

## The plugin

1. A few tools, each safe to call by voice. What cannot be undone asks first and
   acts only on a confirmation token after the owner's own spoken yes.
2. Every tool named after the plugin (`garage_status`).
3. The rules inside the plugin; the bridge's allow-list is a second lock.
4. Its own secrets read from a file it is pointed at, never an environment
   variable the bridge would hold.
5. Starts without a terminal: no questions, only MCP on stdout.
6. A simulated mode, for tests and the first try.
7. Its instructions say when to ask the owner and which tool takes which token.

## Wiring it in

1. `[plugins.<name>]` in `~/.config/claude-voice/config.toml`.
2. The owner runs `claude-voice plugins approve <name>` and types the name.
3. Restart the bridge; `health` shows the plugin `running` after its first call.
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
   the bridge and its first lines are what the voice sees. Then the voice is
   asked what it needs, as
   [docs/voice-feedback.md](../../../docs/voice-feedback.md) describes, and each
   point becomes an issue or a pull request.

Never approve a plugin for the owner, and never put a tool on the allow-list
that the plugin's own session has not called safe for voice.
