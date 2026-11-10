# ADR-CV-010: A plugin already approved is updated without typing, or approved again by voice

## Status

Proposed 2026-11-10, asked for by the owner by voice. Nothing is built until the
owner accepts this record.

## Context

A plugin is a small program on the owner's computer whose tools the voice may
call: the garage door, the TV, the robot vacuum. Today the bridge runs a plugin
only as the owner approved it, by typing its name in a terminal
(`claude-voice plugins approve <name>`). The approval covers two things: how the
plugin is started (its command, arguments, folder and the tools the owner
allowed), and what each allowed tool takes as input.

What holds:

- A new version of a plugin's code, with the same tools taking the same inputs,
  already runs without a new approval. Only a restart of the whole bridge picks
  it up, and that drops the voice call.
- Any change to what a tool takes, however small, hides that tool until the
  owner types the plugin's name again, at the laptop. With three plugins
  improving every day, that is now the most common reason the owner has to go
  back to the laptop.
- The approval also stops a plugin's tools from changing under the voice
  without the owner knowing. That part must stay.

## Decision

Three ways a known plugin changes, each with its own rule. A plugin is known
when the owner has approved its command and folder before.

1. **The same tools, taking the same inputs.** The bridge runs the new code
   without asking, as today, and writes a line to its log. A new voice tool,
   `plugin_restart`, restarts one plugin so the new code is used, without
   restarting the bridge or dropping the call.
2. **A tool added, removed or taking different inputs, or its read-only mark
   changed.** The plugin's checkout must be clean and at the head of the branch
   the bridge follows (main, or the branch the owner chose, such as dreame-mcp's
   `voice`). Then the voice approves it the way it opens the garage door: a
   request tool returns a question naming each new or changed tool and what it
   takes, the voice reads it aloud, and only on the owner's spoken yes does the
   confirm tool take the one-time token. The token expires after two minutes
   and works once.
3. **A new command or folder, or a plugin never approved.** Still typed by the
   owner in a terminal, as today.

The voice approval is accepted only from a call through the Claude app's
connector, the way the owner talks. A call from this computer, which is where
other Claude Code sessions and scripts run, is refused, so no message relayed
between sessions can approve. It is opt-in: off until the owner sets
`voice_approval = true` under `[plugins]` in the settings file.

## Consequences

- The owner approves tool changes from the car or the sofa, and still hears
  each one named before it is allowed.
- Code that is not yet merged can no longer gain tools without the owner typing:
  a plugin's session tries a branch in simulate mode, or the owner types.
- The voice model holds the token between request and confirm, as it does for
  the garage door. The same rule applies: confirm only on the owner's clear yes
  to the question just read.
- The log names each automatic restart and each voice approval, with the
  plugin's commit, so the owner can see afterwards what changed.

## How this sits with the session permissions of the same day

The permission rules the owner set for the six projects let Claude Code
sessions push their own branches and merge their own green pull requests. They
do not touch plugin approval, which lives in the bridge's own approvals file.
No session can approve a plugin: typing stays in the owner's terminal, and the
voice approval refuses every caller on this computer.
