# Plugins: a worked example and what building them taught

## garage-door, the first plugin

```toml
[plugins.garage]
command = "uv"
args = ["run", "garage-door-mcp", "--secrets-file", "firmware/secrets.yaml", "--host", "192.168.0.46"]
cwd = "~/garage-door"
tools = ["garage_status", "garage_request", "garage_confirm"]
timeout = 30
```

- The owner chose these three. `garage_press_button` and `garage_switch` exist
  in the plugin but are left out on his say.
- For a first try, swap the args for `["run", "garage-door-mcp", "--simulate"]`:
  no key, no hardware, and every answer says it is simulated.
- garage-door's instructions say `garage_confirm` comes only after the owner's
  own yes. The bridge puts them in its own instructions and in every garage
  tool's description, because not every client reads server instructions.

## Lessons

- **Keep the connection in a task of its own.** The MCP client is an async
  context whose task group must be closed in the task that opened it. Opened in
  one tool call and closed in another, it fails with "Attempted to exit a cancel
  scope that isn't the current task's". `plugins.Plugin._run` holds it open.
- **A stateful plugin needs one long-lived process.** garage-door keeps the
  token between `garage_request` and `garage_confirm`; a process per call would
  lose it.
- **The service's PATH is short.** `uv` lives in `~/.local/bin`, which the
  systemd service does not have, so the bridge looks the program up itself.
- **The child gets a small default environment** from the MCP library, so
  nothing of the bridge's own, keys included, reaches a plugin.
- **A tool without an output schema answers in text.** The bridge reads that
  text as JSON when it is an object, so the voice gets fields either way.
- **Schemas are read once, at start,** in a thread with its own event loop, so
  the voice sees each tool's real arguments from the first `tools/list`.
