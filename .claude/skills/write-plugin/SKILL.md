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

Never approve a plugin for the owner, and never put a tool on the allow-list
that the plugin's own session has not called safe for voice.
