# ADR-CV-001: A bridge over the Claude Agent SDK, on the user's subscription

## Status

Accepted by Roy Kollen Svendsen, 2026-10-05.

## Context

The goal is to talk to Claude Code on one's own computer while driving.
- Voice assistants on the phone can call remote MCP servers (programs that
  offer named tools to an AI assistant).
- The Claude Agent SDK runs Claude Code from a program.
- When no `ANTHROPIC_API_KEY` is set, the SDK uses the signed-in Claude Code
  account, which bills a subscription rather than per token.
- The owner already pays for a Claude subscription and wanted no API bill.

## Options considered

- **Do nothing; use Claude Code's own Remote Control.** It reaches a terminal
  session from the Claude app, but only by typing or dictating, not in a voice
  conversation.
- **Let the voice provider's real-time API delegate to an agent.** Very good
  speech, but billed per minute on an API account, which the owner ruled out.
- **Wrap Claude Code as an MCP server with a generic stdio-to-HTTP gateway.**
  Claude Code exposes no session control that way, so the gateway would carry
  nothing useful.
- **An MCP server of our own over the Agent SDK.** Chosen.

## Decision

claude-voice is an MCP server that drives Claude Code through the Agent SDK.
It runs as a user service on the owner's computer, signed in as the owner. It
refuses to start while `ANTHROPIC_API_KEY` is set, unless told the API bill is
intended.

## Consequences

- No per-token bill, and the owner's own Claude Code settings and permission
  rules apply.
- The bridge must be reachable from the internet, so it needs its own sign-in
  (ADR-CV-002) and a way for a human to approve risky actions (ADR-CV-003).
- Worse: it depends on the Agent SDK continuing to honour subscription sign-in.
  If that changes, the only fallback is the API bill this was built to avoid.

## Related

README.md, `src/claude_voice/sessions.py`, `src/claude_voice/server.py`.
