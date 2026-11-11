# ADR-CV-013: A measure left outside its limit by agreement is shown as exempt, never green

## Status

Accepted 2026-11-11 by Roy Kollen Svendsen, in the brief he gave the session
that built the code-quality skill: an exemption only with a written reason and
his yes, kept in the repository's `quality.toml`, shown as "exempt" and never
green.

## Context

The software page judges every repository's measures against fixed limits
(`MEASURES` in `src/claude_voice/software.py`): coverage at least 80, every
rule proven, no function above 15, and so on. Some code is better outside a
limit: a function that is one long match on a tool's name reads more clearly
whole than split. Such a measure stays red for ever, and a page that is always
partly red stops being read, so a new problem hides among the agreed ones.

The same rule is written into the code-quality skill in claude-voice,
claude-voice-ui, claude-voice-mobile, garage-door, tv-mcp and dreame-mcp, so
the file and its form are shared by six repositories.

## Options considered

- **Do nothing.** The agreed cells stay red and the page loses its signal. Not
  taken.
- **Raise the limit for one repository.** Hides every future breach of that
  measure there too, not only the agreed one. Not taken.
- **Keep the agreement in the bridge's own settings.** The reason then lives
  away from the code it is about, and a contributor to the plugin cannot see
  it. Not taken.
- **An entry in the repository's own `quality.toml`, with a reason and the
  owner's yes, shown as its own state.** **Chosen.**

## Decision

Each repository may keep a `quality.toml` at its root, with one `[[exempt]]`
entry per measure: `measure` (the page's id), `reason` and `approved`. The
bridge reads it from its clone. An entry with both a reason and an approval
turns a measure that is outside its limit into `exempt`, with the reason at
the end of its text; a measure inside its limit stays `ok`, and an entry
missing either field is ignored. Exempt is never shown as green.

## Consequences

- A cell can be quietened, so the page is only as honest as the approvals.
  The `approved` field is text, not a signature; what makes it the owner's is
  that it lands by a pull request he can see.
- An exemption covers a whole measure, not one function: while complexity is
  exempt for one tangled function, a second one past the limit is hidden
  behind it. Narrowing an entry to one module is left for when that happens.
- The phone page has to know the new state to draw it (claude-voice-ui).

## Related

- [`docs/event-api.md`](../docs/event-api.md), "How the software is doing"
- [ADR-CV-009](ADR-CV-009-main-keeps-the-quality-levels.md), where the levels come from
