# The rules a session is told at its start

A session in one of the owner's organisations follows rules that are not about any one
repository: who to ask, what waits on the owner, what a session may decide alone. They
live in rule packs, and a hook reads them to the session each time it starts. That covers
a fresh start, a resume, a cleared conversation and a compaction, and also each helper
agent the session starts.

## Where the packs are

There are two packs, both in the owner's settings folder and never in any repository:

- `~/.config/claude-voice/orgs/_shared/rules.md`, the lines every organisation shares;
- `~/.config/claude-voice/orgs/<org>/rules.md`, the organisation's own.

The shared pack comes first. Where the two disagree, the stricter line wins, and the
shared pack's own header says so.

## The hook

A role's settings run this, with the bridge's own Python, never a session's:

<!-- not run: a start hook runs it, with the shas of packs the owner has installed -->
```text
python -m claude_voice.org_rules emit <org> --event SessionStart --shared-sha <12> --sha <12>
```

`--event` is `SessionStart` or `SubagentStart`. The two shas are the first twelve hex
digits of each pack's sha256, recorded in the role file when the pack was installed.

The session is given:

```text
shared rules (sha <12>):
<the shared pack>

<org> rules (sha <12>):
<the organisation's pack>
```

## When something is wrong

The hook always exits 0. A hook that fails is silent to the session, so it would start
without its rules and never know. So instead the session is given one line:

```text
ORG RULES <what is wrong>: stop, do no work, and tell your manager in one message
```

That happens when:

- a pack is missing, or is not plain text;
- a pack's sha is not the one the role file recorded, so it changed without an install;
- the two packs together are over 9 000 bytes in the hook's printed output, where each
  character outside plain ASCII is escaped to six bytes. The session itself sees the
  decoded text, so this errs on the safe side. Above about 10 000 characters a session sees
  only a short preview of a hook's context, so the end of the rules would be lost;
- a pack is not a plain file: a folder, or a pipe, which is refused without waiting;
- a pack's file or its organisation's folder is a link;
- the organisation's name is not lower-case letters, digits and dashes, or is `_shared`;
- the hook's own command is wrong;
- anything else fails inside the hook. The line then names the error.

A `rules.md` in the session's folder or its repository is never read.

## Who may change them

No session. `org_rules.py` is in the owner's checkout's deny list, because every role's
start hook runs it as the owner. Installing a pack, and denying writes to the packs, come
in the next changes.
