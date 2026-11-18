# Building a plugin

You need: [SKILL.md](SKILL.md) and [rules.md](rules.md). The guide for people,
[write-a-plugin.md](../../../docs/write-a-plugin.md), says why each file of the template exists.

## 1. Start from a real copy of the template

Make the plugin with `claude-voice new-plugin <name>` (R9). Until that command exists, copy
[templates/plugin/](../../../templates/plugin/README.md) at main into a folder of its own, and
replace `gizmo` with the plugin's name. Never start from a neighbouring plugin's files, which
drift.

The copy is green from its first commit: `<name>_status` runs end to end through the fake
(R10). Keep it green from then on.

## 2. Write the server

1. **A few tools, each safe to call by voice.** Each is named after the plugin, such as
   `garage_status`. What cannot be undone asks first and acts only on a token (R4).
2. **Each tool's first line says what it is for** (R2). Run the cut test from the very first tool,
   not at the end.
3. **Each answer carries a `say`**, the text the voice reads aloud as it is. It is English, since
   the voice handles other languages poorly.
4. **Every error names the cause and the next step** (R1).
5. **Every change is read back** from the device (R3).
6. **The plugin's own secrets** come from a file it is pointed at, never from an environment
   variable the bridge would hold. Personal values live in local config (R5).
7. **It starts without a terminal:** no questions, only MCP on stdout, and fast (R7).
8. **A simulated mode,** for tests and the first try. The fake refuses what the device refuses
   (R6).
9. **Its instructions say when to ask the owner,** and which tool takes which token.

## 3. Write the manifest

`claude-voice-plugin.toml` and `config.default.toml` stay at the root, with made-up values
only. The template's manifest test checks them against the published format.

## 4. Write the docs

`docs/setup.md` with a `Check:` line after each step (R32), the owner's heading (R33), and the
README's step list made from it (R34).

## 5. Keep the rules while building

- A test before the code, and it fails first (R12).
- Each rule marked in the source gets a mutation row and a test named after it (R11).
- Every wait in a test has its own bound (R26), and a child that can fork goes through
  `run_bounded` (R28).
- Every temporary file is removed, a stopped run included (R14, R24).
- The plugin's folder is the running code, so every change is made in a worktree.

## 6. When it is done

Tell the manager it is ready to wire in; [manager.md](manager.md) takes it from there. A
separate reviewer checks it with [review.md](review.md) before it counts as done.
