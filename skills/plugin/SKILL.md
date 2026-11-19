---
name: plugin
description: Make, join, review or backfill a claude-voice plugin, an MCP server on this computer whose chosen tools the voice may call through the bridge. Use it when someone wants a local device, service or script reachable by voice. Use it too when a plugin is added, approved, reviewed or brought up to the template, or a finding cites a rule like R28.
---

# A claude-voice plugin

A plugin is an ordinary MCP server over stdio. The bridge starts it as a process of its own,
offers the voice only the tools the owner approved, and passes on the plugin's own
instructions. Every safety rule lives in the plugin itself.

Three things make one up, and each lives in one place:

- **The files** are in [templates/plugin/](../../templates/plugin/README.md), which is real
  code with its own tests. This skill never holds a copy of them.
- **The rules** are in [rules.md](rules.md), numbered R1, R2 and on. Findings cite them.
- **What a plugin must do once built** is [the plugin standard](../../docs/plugin-standard.md).

## Which part to read

| Your job | Read |
| --- | --- |
| Writing a new plugin, or a new tool in one | [builder.md](builder.md) |
| Bringing a plugin into the team: repo, session, approval, health | [manager.md](manager.md) |
| Reviewing a plugin's pull request against the rules | [review.md](review.md) |
| Bringing an existing plugin up to the template | [backfill.md](backfill.md) |
| A worked example, and what building the first one taught | [reference.md](reference.md) |

Each part says first what to have read. That is normally only this page and rules.md, so a
reviewer never needs the builder's part, and a manager never needs the reviewer's.

## The one rule list

[rules.md](rules.md) is the reference copy. A rule changes there by pull request, and keeps its
number for good; a rule that goes is marked withdrawn.
