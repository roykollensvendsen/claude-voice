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
| `argv` | the command line it last ran with, read only from claude itself, and kept only to compare |

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

`cv up <role>` brings back a role whose session has ended. It is the same
conversation, in a new window of its programme's tmux session, which is named after
the programme's code. The command is built from the owner's role definition, never
from the `argv` the row recorded:

- `claude --resume <id> --settings <the role's settings> --model claude-opus-5-5 --name <role>`;
- the folder comes from the role definition, and the session from the org file;
- no other flag is passed, and no keys are typed into any pane.

When the recorded command differs, `cv up` names the flags that differed, never
their values.

`cv up` resumes only the conversation the bridge itself recorded when it started the
role with `start_role`. It reads that record from `bridge.db`, opened read-only. A role
started by hand has no record, so start it once with `start_role`. A row naming another
conversation is refused, for example after `/clear`.

It also refuses when:

- the conversation is running;
- the conversation id is not a UUID;
- the conversation's transcript is not a plain file under the role folder's own key in
  `~/.claude/projects`, or its first recorded folder is another;
- the role has no definition;
- the role sits in no programme, or in two;
- another `cv up` of the same role is under way.

The shipped permission rules deny Edit and Write on `bridge.db` and the sessions file.

A started claude's command line, settings included, can be read by any process of the
same user. So a role's settings file must never hold a secret.

What `cv up` cannot tell apart: every session runs as the owner's Unix user. A
session that runs code past the Edit and Write denies can still write the database
or a transcript. The denies close the easy path; separate Unix users for narrow
sessions are the real boundary, in a later design.

The design is worker-on-demand-v1, up to v1.6.
