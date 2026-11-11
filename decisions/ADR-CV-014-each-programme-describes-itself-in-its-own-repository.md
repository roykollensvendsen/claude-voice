# ADR-CV-014: Each programme describes itself in its own repository

## Status

Accepted 2026-11-11 by the owner, who approved the design before it was built.

## Context

The owner runs many Claude Code sessions at once. They belong to larger pieces
of work: the voice bridge and its plugins are one programme, each plugin a
component of it. A few sessions hold a role, such as the manager the owner
talks to. The bridge knew none of this, so the voice could not say who handles
what, and the phone page could only list sessions flat or by machine.

Sessions already have two things the bridge can read: the folder they work in
and their name. Neither has to change for a session to be placed.

## Options considered

- **Do nothing.** The owner keeps the picture in his head, and the voice keeps
  asking which session is meant. That is the cost this removes.
- **Tag each session by hand**, in the bridge or on the phone. Every new
  session would start untagged, and a tag kept in the bridge's database would
  be lost to anyone else with the same repositories.
- **One chart in the bridge's own settings.** It would hold personal paths and
  session names, and a programme's structure would live apart from its code,
  where its own sessions cannot read or change it in a pull request.
- **Encode it in session names**, such as `voice-tv-quality`. Every session
  would have to be renamed, and a name would have to change whenever the
  structure did.

## Decision

Each programme describes itself in one optional file, `.claude/org.toml`, in
its main repository: its code, name and aliases, its components with their
folders or git remotes, and its roles with who fills them, their purpose,
limits and what to send them. Folders are relative to that repository, so the
file works in anyone's clone. The bridge reads the files in the folders it
already knows, and places each session from its folder and name. It derives an
address from the result, and accepts that address wherever it takes a session
name, after the name itself. The chart is only read; it changes in the file.
The format is in [the org chart](../docs/org-chart.md).

## Consequences

- A programme's structure changes in a pull request to its own repository,
  where it is reviewed with the code it describes.
- Nothing is placed until somebody writes the file, and a session whose name
  is not in it holds no role. That is deliberate, but the chart can be wrong
  without anyone noticing, so `claude-voice org check` warns about a role whose
  session is not running.
- Worse: an address can stop resolving when a file changes, and short forms
  that named one session can become ambiguous when another programme adds the
  same role. The tools then ask instead of guessing, which costs the owner a
  question.
- The bridge reads one more file per project folder every ten seconds.

## Related

- [docs/org-chart.md](../docs/org-chart.md): the format and the address.
- [docs/event-api.md](../docs/event-api.md#the-org-chart): what the phone page reads.
