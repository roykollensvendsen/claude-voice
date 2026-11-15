# ADR-CV-016: A plugin is installed from a manifest it ships

## Status

Accepted by the owner, 2026-11-15, on a design that quality and voice-architect both
approved.

## Context

Adding a plugin took three steps by hand: an entry in the bridge's settings, the
plugin's own config copied into place with the owner's values, and the typed approval.
Each plugin described those steps in its own README, and each kept its own copy of the
owner's home position. With weather, fuel, easee and grocery plugins coming, the steps
were repeated for each, and a slip in the settings file could break the bridge.

## Options considered

- **Keep the steps by hand.** Nothing new to trust, but every plugin repeats them and
  every slip lands in the owner's settings file.
- **Let the bridge fetch and install a plugin from a URL.** One command, but it brings
  code onto the machine from somewhere nobody vetted at that moment.
- **Run an install script the plugin ships.** Flexible, but the bridge would then run a
  plugin's code before the owner has approved it.
- **A data-only manifest in the plugin's checkout, read by the bridge.** Chosen.

## Decision

Each plugin ships `claude-voice-plugin.toml`, in a published format
([docs/plugin-manifest/v1.json](../docs/plugin-manifest/v1.json)). The bridge reads it
from a local checkout the owner cloned himself, writes only the bridge's settings entry,
the shared home file and the plugin's own config folder, and then asks for the usual
typed approval. It runs only in the owner's own terminal. The home position is kept once,
in `~/.config/claude-voice-home/home.toml`, and each plugin is told the file's path.

## Consequences

- The format is a contract with every plugin. A change that breaks it is a version 2,
  and the bridge has to keep reading version 1 while plugins move.
- The bridge gains a writer for the owner's settings file, and a dependency to write
  TOML, `tomli-w`. A mistake there is worse than a mistake in one plugin's README.
- Install grants nothing: the typed approval is still the gate. So the check that it runs
  in a real terminal is a slip guard, not a security boundary.
- A plugin's secrets stay outside it on purpose. That keeps one manual step per plugin
  that has a key.

## Related

- [docs/plugin-manifest.md](../docs/plugin-manifest.md)
- [ADR-CV-010](ADR-CV-010-a-known-plugin-is-approved-again-by-voice.md), whose approval
  install ends with
- The README's "Installing a plugin in one command"
