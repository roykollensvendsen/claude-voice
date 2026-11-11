# The org chart

When many Claude Code sessions work for one owner, they belong to larger pieces
of work: a programme, such as the voice bridge and its plugins, made of
components, such as one plugin each. A few sessions also hold a role, such as
the manager or the one that looks after quality.

The bridge works this out for every running session from two things it already
knows, the session's folder and its name, and from one optional file per
programme. Nobody tags a session by hand, and no session is renamed.

## What every session gets

| Field | Comes from | Empty when |
| --- | --- | --- |
| programme | the session's folder, matched against the programmes' folders, or its role | the folder is in no programme: the session is shown under "Other" |
| component | the session's folder | the folder is in no component, or the session holds a programme-wide role |
| role | the session's name, listed under a role | the session is an ordinary worker |
| machine | the machine it runs on | it runs on this computer |

A folder inside a component's folder belongs to it, so a git worktree under a
repository is placed with the repository. When folders are nested, the deepest
one wins.

## The address

Each session in a programme also gets an address: the programme, the component
and the role, joined with `/`, leaving out the empty ones. A programme-wide role
has no component, so the manager of `acme` is `acme/manager`, and the quality
role of its arm component is `acme/arm/quality`. A worker on the arm is
`acme/arm`.

Every voice tool that takes a session name also takes its address, or the end
of it, as long as that names one session: `manager`, when only one programme
has one, or `arm/quality`. Each part may be given by its code, its name or one
of its aliases. When the words fit more than one session, the tool answers
with the choices to ask the owner, and never guesses. A session's own name, id
and number keep working as before, and come first.

## The file

One file per programme, `.claude/org.toml` in the programme's main repository.
It is optional; a programme without one is simply not shown. This one, for a
made-up programme, is also in
[examples/acme-robots/.claude/org.toml](examples/acme-robots/.claude/org.toml):

```toml
[programme]
code = "acme"                    # short: lowercase letters, digits and dashes
name = "Acme Robots"             # how it is said and shown
aliases = ["robots"]             # other words the owner uses for it
order = 1                        # where it is listed; optional

[components.arm]
name = "Robot arm"
aliases = ["the arm"]
folders = ["."]                  # relative to this repository: "." is the repository itself
remotes = []

[components.vision]
name = "Vision"
folders = ["../acme-vision"]     # a repository next to this one
remotes = ["acme/vision"]        # or found by its git remote, owner/name

[roles.manager]                  # no component: the role is programme-wide
name = "Manager"
purpose = "Keeps the overview and brings the owner's decisions to the others."
sessions = ["acme-robots"]       # the session names that fill it
limits = "Never merges someone else's pull request without the owner."
send_here = ["decisions", "proposals"]

[roles.quality]
component = "arm"                # a role inside one component
name = "Quality"
purpose = "Watches the arm's tests and measurements."
sessions = ["arm-quality"]
limits = "Changes tests, never the arm's code."
send_here = ["quality", "facts"]
```

- A folder is always relative to the repository that holds the file, never an
  absolute path or one starting with `~`, so the file works in anyone's clone.
  A folder that is not relative is left out, with a warning.
- A remote is matched against the `git remote` addresses of the session's
  folder, when no folder matched.
- `send_here` says what to send to the role: `facts`, `proposals`,
  `decisions` or `quality`.

These org roles are not the bridge's start roles (`roles.toml`, such as
`contribute`), which say how to start a new session for a task.

## Which files the bridge reads

Only files in folders it already knows: its own clone, the plugin folders in its
settings, and the project folders it lists (every folder directly under its
project root). It looks again every ten seconds, so an edited file is seen
without a restart.

## Checking a file

`claude-voice org check` reads the same files the bridge reads. Here it is with
the example as the only project, on a computer where none of its sessions runs:

```console
$ env HOME=/nonexistent CLAUDE_VOICE_ROOT=docs/examples claude-voice org check
acme: component vision: folder ../acme-vision does not exist
acme: role manager names session 'acme-robots', which is not running
acme: role quality names session 'arm-quality', which is not running
```

It warns when a role names a session that is not running, a folder that does
not exist, a component the file does not declare, or something to send that is
not one of the four. These are warnings, about how things stand now. A mistake
in a file itself, such as one that cannot be read, a programme with no code or a
folder that is not relative, is an error, and the command then exits with 1.

`claude-voice org` prints the chart as the voice hears it:

```console
$ env HOME=/nonexistent CLAUDE_VOICE_ROOT=docs/examples claude-voice org
Acme Robots (acme): manager, acme-robots, not running; send decisions, proposals.
Acme Robots (acme): arm quality, arm-quality, not running; send quality, facts.
```

## Who reads it

- The voice: the `org_chart` tool gives the whole chart in a few short lines,
  one per role, and one more for the sessions in no programme.
- The phone page: each session in `GET /api/v1/sessions` carries its fields,
  and `GET /api/v1/org/{programme}` gives a programme's roles, who fills them
  and which have no running session; see
  [the event API](event-api.md#the-org-chart).

Both only read. The chart changes in the file and nowhere else.
