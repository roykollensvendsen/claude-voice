# ADR-CV-011: A short, fixed list of the owner's own actions is approved by voice

## Status

Accepted 2026-11-10 by the owner, by voice. It builds on
[ADR-CV-010](ADR-CV-010-a-known-plugin-is-approved-again-by-voice.md).

## Context

The owner runs six repositories through one manager session: the bridge, its
phone page, the phone app and three plugins. The sessions do the work and merge
their own green pull requests. A few small actions are still the owner's alone,
and each one sends him back to a terminal to paste a command: turning on a
repository's documentation site, closing a pull request that a newer one
replaced, making a check required, running a failed check again.

None of these can be left to a session. A session is told what to do by
messages from other sessions, so anything a session may do, a message can ask
for. That is why the approval has to come from the owner himself.

ADR-CV-010 already has the shape for this: a request tool reads a question
aloud, the owner says yes, and a confirm tool takes a one-time token. It is
accepted only from the Claude app, in a developer mode the owner switches on.

## Decision

The bridge carries out a fixed list of named actions, each only after the
owner's spoken yes, in the same way as approving a plugin by voice.

| Action | What the bridge checks before it asks |
| --- | --- |
| Turn on a repository's documentation site | The site's files pass a privacy scan: no private addresses, machine names, e-mail addresses or photo locations. The question names what the scan found, or that it found nothing. |
| Close a pull request that another one replaced | It was opened by the owner's account or a session, never by a contributor. The question names the pull request that replaced it, and the closing comment says where the work landed. |
| Make a named check required | The check exists in the repository's workflow and has passed on the main branch. Checks are only added, never removed. |
| Run a failed check again | The run belongs to an open pull request in one of the six repositories. |

The rules from ADR-CV-010 apply to every action:

- It works only in developer mode, and only from a call through the Claude app.
  Calls from this computer are refused, so neither a session nor a relayed
  message can approve.
- The question names exactly what will happen: the repository, the pull
  request or check, and the outcome.
- The token works once and expires after two minutes.
- Every request, approval, refusal and result is written to the bridge's log.

Every action works only on the six repositories in the bridge's own list. A
repository, check or pull request outside that list is refused before any
question is asked.

These stay typed by the owner and are never offered by voice:

- changes to a session's own permission settings;
- secrets and keys;
- anything that costs money;
- deleting a branch, and force pushes;
- removing a required check or loosening a branch's protection;
- anything outside the six repositories.

## Consequences

- The owner clears the small administrative steps from the car or the sofa, and
  hears each one named before it happens.
- The bridge now holds a little power over GitHub, using the owner's own login.
  The fixed list and the checks before each question are what keep that power
  small. A new action needs a new decision record, not a configuration change.
- A wrong yes can turn on a site or close a pull request. Both can be undone
  from GitHub, which is why only actions that can be undone are on the list.
- A privacy scan can miss something a person would see. The question says what
  was scanned, so the owner knows what the yes covers.
