# Changelog

Every release, what changed in it, and why. Dates are the tag's date. The
format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and
versions follow [semantic versioning](https://semver.org/spec/v2.0.0.html).

## Unreleased

### Added

- An MCP server that starts, follows, resumes and stops Claude Code sessions
  through the Claude Agent SDK, on the owner's subscription.
- Sign-in with OAuth for phone apps, and the login secret as a bearer token for
  local tools.
- Risky actions wait for a spoken approval and are refused after ten minutes.
- Sessions open elsewhere can be listed, messaged, read and searched.
- `whats_new`, a cheap delta of what happened since the last poll.
- `CLAUDE_VOICE_ACCESS_TOKEN_SECONDS` sets how long an access token lasts, for a
  bridge on a computer that sleeps: the Claude app gives a connector up after about
  an hour of failed token refreshes.
- The log is kept in `bridge.log` next to the database as well as on the console,
  with the outcome of every token refresh, so a connector that dropped while
  nobody watched can be diagnosed afterwards.
- `wait_for_session` waits, sending nothing, until a running session's turn ends
  or it needs its user, so a voice assistant can wait for a reply instead of polling.
- `claude-voice check` and `claude-voice serve`, and a user service installer.
- A session's choice box can be answered by voice while the phone hook waits on it.
  `list_pending_approvals` also lists these questions with numbered options,
  `answer_question` answers one, and `whats_new` reports a new one as `needs_answer`.

### Security

- A plugin's approval now covers every word the voice reads from it: each tool's
  description and the plugin's own instructions. Before, a changed plugin could rewrite
  them and tell the voice to do something without being approved again. After upgrading,
  every plugin is approved once more, typed or by voice in developer mode.
- `claude-voice plugins list` checks a plugin the way a restart does, so it no longer
  says a plugin is approved while the bridge refuses its changed tools.
