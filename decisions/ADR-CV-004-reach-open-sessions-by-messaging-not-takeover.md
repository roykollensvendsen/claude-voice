# ADR-CV-004: Reach sessions open elsewhere by messaging, never by taking them over

## Status

Accepted by Roy Kollen Svendsen, 2026-10-05.

## Context

The owner wanted to speak to sessions already open in a terminal.
- Resuming such a conversation from the SDK starts a second copy, and the two
  then diverge.
- Tried against an open session, a resumed turn did nothing at all.
- Claude Code sessions on one machine can message each other with the official
  SendMessage tool, and the message lands in the open session itself.
- Each session's transcript is on disk, and the SDK can read it.

## Options considered

- **Resume the conversation.** Yields a copy, not the session.
- **Speak Claude Code's local session socket protocol directly.** It is
  undocumented and may change with any release.
- **Share the terminal (tmux).** Keyboard only, not voice.
- **Deliver with a short helper session limited to the messaging tools, and read
  replies from the transcript.** Chosen.

## Decision

- `message_active_session` starts a helper session whose only permitted tools
  are ListAgents, SendMessage and ToolSearch, and has it deliver the text.
- `read_session_output` and `search_session_history` read the target's current
  transcript, found through Claude Code's registry of running sessions.
- The bridge never stops a terminal session.

## Consequences

- It uses only official tools, and the session receives the message as a
  colleague's request, within its own permissions.
- Each delivery takes ten to twenty seconds and a little subscription use.
- Worse: replies are read rather than received, so the owner hears an answer
  only by asking for it, or through `whats_new`.

## Related

`src/claude_voice/transcripts.py`, `src/claude_voice/events.py`.
