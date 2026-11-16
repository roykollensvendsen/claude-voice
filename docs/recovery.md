# Bringing sessions back

When the desktop session ends, every Claude Code session in a terminal window
ends with it. Sessions in tmux keep running. The decision behind this page is
[ADR-CV-019](../decisions/ADR-CV-019-sessions-live-in-tmux-and-a-private-file-lists-them.md).

## The sessions file

The bridge keeps one row per role in `~/.local/state/claude-voice/sessions.toml`.
Only its owner can read it, because it names conversations.

| Field | Holds |
|---|---|
| `organisation` | the programme the session belongs to in the org chart, or `solo` |
| `role` | the session's name |
| `folder` | the folder it works in |
| `conversation` | the Claude Code conversation it runs, which `claude --resume` takes |
| `tmux` | its tmux pane, as Claude Code writes it, or empty when it is not in tmux |
| `last_seen` | when the bridge last saw it running, in UTC |

The bridge writes it as it looks at the running sessions, every few seconds.
A session that starts, changes its name or begins a new conversation is written
at the next look. A conversation is listed under one role only, so a renamed
session moves to its new row. A row stays when its session ends: that is the
row recovery needs.

Only sessions in a terminal are listed. The bridge's own background helpers are
not roles.

## `cv`

`cv` reads the sessions file. It is installed with the bridge, and works the
same over SSH, for example from a phone.

<!-- not run: it reads the owner's own sessions -->
```
cv ls
cv peek manager
cv peek voice/manager
```

- `cv ls` prints one line per role, under 40 characters, so it fits a phone:
  its organisation, its role, and `busy`, `idle`, `needs you` or `gone`.
  `gone` means no running session has that conversation.
- `cv peek <role>` prints the last 20 lines of the role's tmux pane, without
  attaching to it and without typing anything into it. A role whose name is
  used in two organisations is named as `<organisation>/<role>`.

Both say what to do when they cannot answer, such as when the role is gone or
not in tmux.
