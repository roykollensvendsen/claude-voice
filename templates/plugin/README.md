# gizmo-mcp

A made-up gizmo, read by voice. This is an MCP server that a voice assistant reaches through
claude-voice, as one of its plugins.

It started from claude-voice's plugin template. The guide for writing one is
`docs/write-a-plugin.md` in claude-voice, and `docs/plugin-standard.md` there holds the rules.

## Try it

<!-- not run: it needs the project installed, which `just setup` does -->
```
just setup
uv run gizmo-mcp status --simulate
```

The second line prints what the voice would hear, from the made-up gizmo in `fake.py`.

## Install it in claude-voice

<!-- not run: it changes the owner's own bridge config, and asks in a terminal -->
```
claude-voice plugins install gizmo --from <this folder>
```

It reads `claude-voice-plugin.toml`, writes `config.default.toml` as your first config, and
ends with the usual typed-name approval.

## What is where

| Part | What it is for |
|---|---|
| `src/gizmo_mcp/server.py` | The MCP tools. Each answer carries a `say`, the sentence the voice reads aloud. |
| `src/gizmo_mcp/service.py` | The core: it asks the gizmo and words every answer, failures included. |
| `src/gizmo_mcp/device.py` | What the service needs from a gizmo. A real adapter answers it. |
| `src/gizmo_mcp/fake.py` | A made-up gizmo that refuses what the real one refuses. |
| `src/gizmo_mcp/settings.py` | The plugin's own config, from its own folder. |
| `src/gizmo_mcp/platform/` | The one place that knows the operating system. |
| `scripts/ratchet.py` | The quality levels, which may never get worse than main's. |
| `scripts/mutate.py` | Switches each rule off in turn, to prove its test can fail. |

## The checks

Every check is a recipe in the `justfile`, and CI runs the same lines.
`just check` runs the ones a pull request needs, with no network.

<!-- not run: these are the gates; running them from a gate would recurse -->
```
just check
just commits
just ratchet
```

A new test must fail on the code before it, and the `red-first` job proves that. It runs only
the test files a change touches. So it does not see a change to shared test setup, such as
`tests/conftest.py`; say that in the pull request.

A rule is a line marked `# RULE: what it says`. Each one needs a test with its words as the
name, and a row in `scripts/mutations/`.

A commit may carry `Co-Authored-By` and an outside contributor's `Signed-off-by`, and no other
trailers. Any other note
belongs in the pull request's text. `just setup` turns on the hook that says so early.
