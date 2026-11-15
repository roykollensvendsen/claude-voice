# ADR-CV-017: A profile is an allow-listed shape, and the values stay with the user

## Status

Accepted by Roy, 2026-11-15, with the platform-setup design that quality reviewed.

## Context

Setting up claude-voice meant reading four documents and writing each plugin's settings by
hand, often from another user's README. A developer wants one file to say which
repositories and plugins they get, and to share it. A shared file travels, so whatever is
in it is public once. The settings for a home plugin hold exactly what must not travel: a
home's position, a device's address, a charger's number, an account name.

## Options considered

- **Do nothing.** Every user keeps writing settings by hand. Nothing leaks, and nothing is
  shared either; each new user starts from a blank page.
- **A profile with real values, and a scan for secrets.** Easy to write. A scan can only
  refuse what it knows to look for, so a room's name or a host on a home network passes.
- **A profile with templates, checked by an allow-list** (chosen). Every word must be a
  placeholder, a short list of generic words or the name of one of the profile's own public
  repositories. Every number must be a documented default. Comments start their line and
  are scanned, since a TOML parser drops them unread.

## Decision

A profile names public repositories and plugin templates, never a value. Values fill each
`{placeholder}` from the user's own `values.toml`. The default profile ships inside the
package, so an installed command finds it without a clone. The user's profile and this
machine's `profile.local.toml` are laid over it, adding, replacing or removing by name.
`profiles.problems` is the guard, and `tests/test_profiles.py` runs it over every profile
shipped here.

## Consequences

- A user can share their profile without sharing their home.
- **What gets worse:** the allow-list refuses some harmless words, and a template author
  must add a word to `SAFE_WORDS` in a reviewed change, or use a placeholder.
- A plugin's tool names must be built from allowed words too, so some plugins need a
  placeholder or a list change before they can go in a profile.
- A repository's name is trusted as public, because its address is on the public host. A
  public repository named after a private thing would pass.

## Related

- [docs/setup.md](../docs/setup.md), which says what a profile may hold.
- `src/claude_voice/profiles.py` and `src/claude_voice/default_profile.toml`.
