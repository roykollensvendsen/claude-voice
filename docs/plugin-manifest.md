# The plugin manifest

A plugin ships `claude-voice-plugin.toml` at its repository's root, so that
`claude-voice plugins install <name> --from <checkout>` can add it in one go. The
manifest is data only: the bridge reads it with `tomllib` and never runs it. Its format
is published as [plugin-manifest/v1.json](plugin-manifest/v1.json), a JSON schema, and a
plugin's own CI validates its manifest against it. The example is illustrative: a
plugin lists the tools it really serves, so that its entry matches the one already in use.

<!-- not run: a manifest, not commands -->
```toml
manifest = 1                       # the format's version
name = "fuel"                      # its [plugins.<name>] entry: lowercase, digits and _
command = "uv"
args = ["run", "fuel-mcp", "serve"]
tools = ["fuel_price"]             # the tools the voice may call: exactly those it serves
timeout = 30                       # seconds a call may take; 30 when left out
branch = "main"                    # the branch its checkout must be on; main when left out
uses = ["home"]                    # shared values it reads; only "home" exists
secrets_step = "uv run fuel-mcp login"  # optional: named in the summary, never run

[config]
default = "config.default.toml"    # in the checkout, with made-up values only
dir = "fuel-mcp"                   # becomes ~/.config/fuel-mcp/config.toml

[[config.personal]]                # asked in the terminal, not shown, written into that config
key = "sources.min_age_minutes"
type = "int"
range = [0, 1440]
ask = "Ignore prices older than how many minutes?"
```

## What is checked

- **Every key is known.** A key the format does not have refuses the manifest, at the top,
  in `[config]` and in each `[[config.personal]]`.
- **`name`** matches the bridge's plugin names, and **`tools`** names at least one tool.
- **`uses`** holds only `home`. A plugin that uses it needs a `[config]`: install writes
  `home_file = "~/.config/claude-voice-home/home.toml"` into the plugin's own config, and
  the plugin reads its home position from there, refusing the file unless it is 0600 and
  its owner's. The bridge passes nothing.
- **`dir`** is one folder name, `[a-z][a-z0-9-]{0,40}`, and never one the system or the
  bridge uses, such as `claude-voice`, `systemd` or `autostart`. Install also requires it
  to be the plugin's `name` or its checkout folder's name, and asks before writing to a
  folder that no plugin in the bridge's known list uses.
- **`default`** is a path inside the checkout, never through `..` or a symlink.
- **A personal field** has a `key` (dotted, never `home_file`), a `type` and an `ask`.
  The types are `int` and `float` (with an optional `range`; `round` for a float, at most
  6), `str` (with `max_len`) and `choice` (with `values`). A key that looks like a secret,
  such as `password`, `token` or `api_key`, is refused: a secret goes through the plugin's
  own `secrets_step`, never through install.

## Checked by the bridge only

The schema cannot see the checkout or the owner's folders, so these are checked only by
`plugins install`:

- `dir` must be the plugin's `name` or its checkout folder's name;
- a `range` must have its lowest value first;
- the checkout must be a clean git checkout on `branch`;
- no step of the plugin's config path, or of the default's path inside the checkout, may be
  a symlink;
- the plugin's config and the home file may not land inside any git repository, which a
  `~/.config` linked into a dotfiles repository would otherwise make happen.

Every other rule is in the schema, and the bridge's own check follows it. A test requires
a case for each of the schema's constraints and puts each case to both.

## How the bridge starts it

A plugin declared as `command = "uv"` with `args = ["run", "<script>", ...]` is not started
through `uv run`. That would keep uv running as the plugin's parent, holding uv's cache lock
for as long as the plugin runs, so the cache could never be pruned. Instead, after the
approval is checked, the bridge:

- runs `uv sync --locked` in the plugin's folder, which makes `.venv` match its `uv.lock` and
  makes a missing `.venv`. It waits up to five minutes in all, waiting for its turn behind the
  bridge's other uv work included. When the time runs out it stops, with everything it started;
- starts `.venv/bin/<script>` with the rest of the arguments (on Windows,
  `.venv\Scripts\<script>.exe`).

A failed sync, or a script missing after it, refuses the start. The refusal names the folder
and says to run `uv sync --locked` there, and the voice is told it when it restarts the plugin. The script must be a plain name: no slash and no
`..`. Its path must also stay inside `.venv`. Any other command starts as it is written.

The approval covers the command as the manifest writes it, so this needs no new approval.
