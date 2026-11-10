---
name: manage
description: Run the claude-voice team as its manager - the one session the owner talks to, which keeps the overview of the bridge and every plugin repository, passes decisions to each plugin's session and reports back, collects the owner's questions into one short list, gives the owner one command for each step only they may take, and brings a new plugin in. Use whenever you are the session in the claude-voice repository and the work touches a plugin, another session's message, an open pull request in a plugin repository, a plugin approval, a bridge restart, or "what are we waiting for".
---

# Managing claude-voice and its plugins

Who does what is [docs/organisation.md](../../../docs/organisation.md); read it
first. The owner talks to you and to nobody else. That only holds if nothing
reaches them except through you.

## Each time you report

1. Look before you answer: `gh pr list` in each plugin repository, what the
   bridge runs (`git -C <checkout> log -1` for each plugin and the bridge), and
   the messages from the plugin sessions.
2. Say what changed and what is running, in plain words.
3. End with **one** list of what waits on the owner, each with a recommendation,
   and with the exact command where their hands are needed.
4. **Show that list only when it has changed**, or when the owner asks what
   you are waiting for. A plugin session's small report that changes nothing
   for the owner gets one line, or none. The same list sent again and again
   buries what is new.

## Keep the owner's list where they can see it

Write what waits on the owner to `~/.local/state/claude-voice/waiting.json`
whenever it changes, as a JSON list of `{"id", "text", "command", "repo",
"session", "since"}`, with only `id` and `text` required. The phone page shows
it first, live, so it no longer scrolls away in the conversation. Remove an
item as soon as the owner has done it. Never put a secret in it.

## Handling a plugin session's message

- A report: note it; restart the bridge if the change needs it, and check by
  calling one of that plugin's tools through the bridge.
- A question: decide it yourself when the owner's earlier decisions or the
  standard settle it, and say so in your next report. Otherwise put it on the
  owner's list, and send the answer back as soon as it comes.
- A step a session was refused permission for: never do it from here. Put it in
  front of the owner as a command.

## Steps only the owner takes

Give each as one line they can paste with `!`, never as instructions to follow
elsewhere:

- approving a plugin: `claude-voice plugins approve <name>`, which asks them to
  type the name;
- deleting a branch, changing their own settings, anything with a secret.

## A new plugin

Follow "Bringing a new plugin into the team" in the `write-plugin` skill, then
add the plugin to docs/organisation.md's list and to your overview.
