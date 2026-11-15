# The workflow event, version 1

The bridge keeps a local log of where work gets stuck: a review that waits, a
check that fails at random, a session that stalls. Each line of that log is one
event. This page says what an event holds and how it is read back.
[workflow-event/v1.json](workflow-event/v1.json) is the same format as a JSON
schema, for other programs.

The log stays on the owner's computer. Nothing from it goes into a repository,
a pull request or public text.

## Where an event comes from

One collector reads each source, such as the pull requests or the live
sessions. A collector never writes the log. It hands each thing it saw to the
gate, which lets fields through by an allow-list:

- **The session and the repository** are the bridge's own, from its programme
  files. A collector names a place, and the gate looks it up. A place the
  bridge does not know is refused.
- **The source** is the collector's registered name, set by the bridge.
- **The kind, the blocker and the state** come from fixed lists. Anything else is
  refused.
- **`what`** is a path the repository's git knows, a rule name, or a word from
  the collector's own vocabulary. Anything else becomes the collector's
  catch-all word, or null when it has none. Each such `what` is counted per
  source, so a collector naming words it does not own shows up.
- **The line** is one line of at most 120 characters. Text shaped like a token
  is blanked first: `sk-`, `ghp_`, `gho_`, `github_pat_`, `Bearer`, long base64
  or hex runs, and links with a query string. A line holding private data is
  dropped whole: an email address, exact coordinates, a charger ID, a home
  address or one of the owner's private words.
- **The key** must be plain text and hold no private data, because it goes into
  the id. A key that does is refused.
- **The id** is the source, the collector's own key, and the friction's start to
  the second, written in UTC. A close carries the same id as its open, even when
  the two came from clocks with different offsets. A second stall of one session
  gets a new id.

Two limits of the private scan:

- **A private word needs at least four letters** to be matched. A shorter one
  would match ordinary text, so it is ignored. Write the owner's private words
  with four letters or more.
- **Coordinates are caught as a pair**, latitude and longitude, with three or
  more decimals. A lone exact number is not.

## A collector's vocabulary

A collector may name its own words, such as reviewer names, as a frozenset
written out in its source. The gate accepts those words as `what` from that
collector only. Each word is at most 40 characters of `a-z`, `0-9` and `-`.

A collector with a vocabulary also names its own catch-all, one of those words,
such as `other-reviewer` or `other-tool`. The gate turns any other `what` from
that collector into the catch-all, so an unknown word is never free text.

## The fields

The schema describes each field. A few rules need saying here:

- `v` comes first. A reader accepts its own version and the one before. It
  skips any other version and counts it, and never guesses. Version 1 has no
  version before it.
- A line that does not fit its version is skipped and counted too.
- Every time carries its offset.
- `duration_s` is set only on a closed event.
- `closed_late` appears only on a close the bridge saw after the friction had
  ended while it was down.

## A new version

A new version is a new file next to `v1.json`, which is never edited once
published. The reader for the version before it stays, with a test that an old
log still reads.
