# ADR-CV-020: Approving a plugin may rewrite its tools list, and nothing else, in place

## Status

Accepted 2026-11-19, with the approve-new-tools design that quality and the architect
reviewed.

## Context

The owner's settings file lists, for each plugin, the tools the voice may call. The owner
writes that file by hand and keeps comments in it. When a plugin gains a tool, the owner had
to open the file, add the name, and approve again. Until now the command line only read the
file; `plugins install` wrote whole blocks into it, but never changed a line the owner wrote.

## Options considered

- **Do nothing.** The owner edits the list by hand. Nothing is overwritten, but a typo in a
  tool name fails quietly, and every new tool costs a trip to an editor.
- **Keep approved tools in a file of the bridge's own.** The owner's file is never touched.
  But there are then two lists for one plugin, and the one the owner reads is no longer
  the one that counts.
- **Rewrite the one list in place, on a typed count** (chosen). `garage +1` adds the tools
  shown; the plain name adds nothing.

## Decision

`claude-voice plugins approve` may change one thing in the settings file: the `tools` list of
the plugin being approved. It adds only tools the owner was just shown and counted. It drops
only tools the plugin no longer offers. Comments, order and every other line stay.
Before writing, it takes a lock that `plugins install` takes too, and checks that the file
is still as it was when the screen was built. It keeps the file's mode, follows a symlink to
its target, and backs the file up first; the last five backups stay. After writing, it reads
the file back and puts the backup back if the result is not exactly the list intended.

## Consequences

- A new tool is one typed line away, and the owner's file stays the only list.
- **What gets worse:** the command line now writes a file the owner edits by hand. A change
  made while the screen is open stops the approval, and it must be run again.
- Adding is refused from a pipe, so a script cannot widen what the voice may call.
- Backups gather beside the settings file, five at most.

## Related

- [README.md](../README.md), the list of what can happen to an approved plugin.
- `src/claude_voice/tool_list.py`, and `safefile.locked` in `src/claude_voice/safefile.py`.
