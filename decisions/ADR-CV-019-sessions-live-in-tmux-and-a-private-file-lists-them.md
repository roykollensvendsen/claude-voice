# ADR-CV-019: Sessions live in tmux, and a private file lists them for recovery

## Status

Accepted 2026-11-16 by the owner.

## Context

On 2026-11-15 the desktop session ended. Every Claude Code session in a
terminal window ended with it. The sessions running in tmux survived, and so did
the bridge.

Bringing the lost sessions back took a person reading old conversations to
find out which session had been which. Nothing on the computer said which role
ran in which folder, under which conversation.

Claude Code already writes a small record for each running session. It holds
the session's name, folder and conversation, and the tmux pane when it runs in
one. That record is deleted when the session ends, which is exactly when it is
needed.

## Options considered

- **Do nothing.** The next logout costs the same hours again.
- **Keep the terminal windows, and save their layout.** A window manager's
  layout says nothing about which conversation ran where, and the windows still
  die with the desktop.
- **Read the conversations Claude Code keeps on disk.** They say which folder a
  conversation ran in, but not which role it was, and many old conversations
  share a folder.
- **Start every session in tmux, and keep a file of who ran where.** tmux
  outlives the desktop, and the file outlives the sessions.

## Decision

Every session the bridge starts runs in tmux: one tmux session per
organisation, one window per role. A desktop terminal only attaches to it.

The bridge keeps a private file, `~/.local/state/claude-voice/sessions.toml`,
with one row per role. A row holds the organisation, the role, its folder, its
conversation, its tmux pane and when it was last seen running. The bridge
writes a row whenever it sees a session start, change its name or begin a new
conversation. A row stays when its session ends, so recovery reads only this
file.

A small command, `cv`, reads the file. It lists the roles and shows the last
lines of one, and it will later attach to one and bring back the missing
ones. It works the same locally and over SSH from a phone.

## Consequences

- A session left out of tmux, such as one started by hand in a desktop
  terminal, is listed but cannot be shown or attached to.
- The file holds conversation ids, so it stays private: owner-only, outside
  any repository, never sent anywhere.
- The file is only as fresh as the bridge's last look. A session that starts
  and ends while the bridge is stopped is never listed.
- Worse: a role renamed outside the bridge leaves its old row until the new
  name is seen, so a listing can show both for a few seconds.
- It is built in steps. The file and the read-only commands come first. Every
  start going through tmux, attaching, and recovery in one step come after.

## Related

- [docs/recovery.md](../docs/recovery.md), what the file and `cv` do today.
- [ADR-CV-012](ADR-CV-012-a-session-opens-where-the-owner-is.md), which
  already opens a session in tmux by attaching to it.
