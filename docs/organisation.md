# How the work is organised

claude-voice is the bridge, and each plugin is a small server of its own in a
repository of its own: the garage door, the TV and the robot vacuum today. Each
repository has a Claude Code session working in it. This page says who does
what between them, so that the owner talks to one session only.

## One manager

The session working in claude-voice is the **manager**. The owner talks to it,
by voice or in the terminal, and to no other session.

The manager:

- keeps the overview: which pull requests are open where, what the bridge runs,
  and how far each plugin is from [the plugin standard](plugin-standard.md);
- passes the owner's decisions to the session they concern, and their reports
  back;
- collects every decision for the owner into one short numbered list at the end
  of an answer, with a recommendation, and decides routine matters itself;
- runs each step itself once the owner says yes, so the owner decides and never
  has to open another session;
- speaks up when the work does not flow, and proposes the change that fixes it;
- starts the session for a new plugin, and brings it in as below.

## The plugin sessions

Each plugin's session owns its repository, and nobody else edits it. A plugin
session:

- **never asks the owner directly.** It sends the manager the question, its
  options and its recommendation, in a few lines;
- works test first, one topic per pull request, and merges its own pull request
  by rebase once every check is green;
- tells the manager when a pull request opens and when it lands, and whether the
  bridge needs a restart or a new approval for it;
- gives the manager the exact command for anything it was refused, and never
  works round the refusal.

Technical matters go straight between sessions, by message. The manager is not
a go-between for those; it needs only the outcome.

## What stays with the owner

The owner makes these decisions. Doing the step does not stay with the owner:
the manager runs it on their yes, unless typing is the point, such as a name typed
to approve, a secret or a login. These are the decisions:

- approving a new plugin, by typing its name (`claude-voice plugins approve`), and
  approving a plugin whose tools changed, by voice in developer mode or by typing.
  No session can approve: the bridge refuses every caller on this computer;
- anything that moves something physical outside the plugin's own two-step
  question, such as arming the garage door;
- secrets, which never pass through a session;
- money, billing, network access and pairing a machine;
- a major dependency upgrade, a change to the protocol, and merging someone
  else's pull request.

Everything else the manager decides, or the plugin session decides and reports.
What the organisation made, it cleans up itself. A branch the owner made by hand
is never touched.

## Freedom under responsibility

Every session does the side tasks its own job needs without asking: re-running a
check, rebasing its own branch, cleaning up what it made. It never works round a
refusal; it sends the manager the exact command instead. When the work does not
flow, it tells the manager in one message what stopped it, how often, and what it
proposes. The manager does the same with the owner.

## Bringing in a new plugin

The [`write-plugin`](../.claude/skills/write-plugin/SKILL.md) skill has the
steps, so that a new plugin starts at the level the others reached: its
repository set up and locked like the rest, a session of its own that reports
to the manager, the standard's gaps listed, and the owner's approval asked for
once, by one command.
