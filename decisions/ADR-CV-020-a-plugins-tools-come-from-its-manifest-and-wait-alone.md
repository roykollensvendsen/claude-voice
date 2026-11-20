# ADR-CV-020: A plugin's tools come from its manifest, and a new or changed tool waits alone

## Status

Proposed 2026-11-20, as plugin-config-commands-v1, designed by the architect; the
owner accepts it with the pull request. It extends
[ADR-CV-010](ADR-CV-010-a-known-plugin-is-approved-again-by-voice.md). The first
approval of a plugin by voice (D1 in that design) is the owner's to decide, and is
not part of this record.

## Context

A plugin is a small program on the owner's computer whose tools the voice may call.
The bridge runs one only as the owner approved it: its command, folder and list of
tools, and what each tool takes and says.

What held on 2026-11-20:

- When a plugin gained a tool, someone pasted its new tool list into the bridge's
  settings file by hand. The owner said by voice that they do not want to paste
  settings any more. That day the manager wrote two plugins' entries by hand, on
  the owner's yes.
- Any change to the list stopped the whole plugin until the owner approved again.
  So adding one tool took a working plugin away from the voice.
- The branch a plugin's checkout follows was not part of the approval. A voice
  approval checked that the checkout was level with that branch, but trusted the
  checkout's own record of where its remote stood.
- Installing a plugin always asked questions, so it ran only in the owner's own
  terminal, even for a plugin that needed no answer.

## Options considered

- **Do nothing.** Every new tool keeps costing a hand edit of a file that runs as
  the owner, and stops the plugin until they approve.
- **Let sessions write the settings file.** It decides what runs as the owner; a
  session's typo or mistake there runs too. Refused in runs-as-owner v1.3.
- **Approve a new tool when it appears in the manifest.** The manifest is the
  plugin's own code, so a change to a plugin would widen what the voice can do
  with no one asked.
- **Commands that write only what the plugin's reviewed code says, and never
  approve.** Chosen.

## Decision

A plugin's tools come only from its own manifest, read from a checkout that is clean
and level with its branch, by `claude-voice plugins tools <name> --from-manifest`.
It adds only tools new to the manifest since the owner's last approval, so one left
out on purpose stays out. `plugins set` no longer takes a list of tools. A tool that
is new, or whose inputs or words changed, waits alone while the plugin's other tools
stay offered; the owner hears about it once and approves it as before. A change to
the plugin's command, folder or branch still stops it, and the branch is now part of
what is approved. A voice approval also asks GitHub for the tip of that branch, for
the repository in the bridge's own list of known plugins, at the question and again
at the yes. `claude-voice plugins install <name> --no-ask` installs a known plugin
that needs no answer, from its folder in the owner's home, with no one at a terminal.
None of these commands approves anything.

## Consequences

- A session can add a plugin's new tool without the owner's hands, and nothing the
  voice can call widens until the owner says yes.
- A plugin keeps working while a tool waits, so a change never takes it away.
- A plugin not in the bridge's list of known plugins can no longer be approved by
  voice; it is typed. The same holds when GitHub cannot be read.
- Approvals from before the branch counted were given the branch in the settings
  once, at the first start after the upgrade. Whatever stood in the settings that
  day is approved; that was already what ran, and the owner heard which ones.
- Worse: a plugin approved before the manifest's list was kept cannot have its
  tools set from its manifest until it is approved once more.
- Worse: the bridge now keeps two more small files, the list of waiting tools it
  has told the owner about, and the copies of the settings file the command keeps.

## Related

- [README: Plugins](../README.md#plugins-other-local-tools-for-the-voice)
- [ADR-CV-010](ADR-CV-010-a-known-plugin-is-approved-again-by-voice.md)
- [ADR-CV-016](ADR-CV-016-a-plugin-is-installed-from-its-own-manifest.md)
