# Permission rules for the voice repositories

A Claude Code session asks before every command it has no rule for. In the
bridge's repositories that means a question for each `git log` and each test
run, and from a phone each one is a tap. These rules let a session look and
test without asking, and refuse what must never happen without the owner.

They live in [`src/claude_voice/permissions.json`](../src/claude_voice/permissions.json),
in named groups, so they ship with the bridge. One command adds them to each
repository:

<!-- not run: changes the settings of the reader's own clones -->
```
claude-voice permissions --dry-run      # say what it would add
claude-voice permissions                # add it
```

With no folders named it uses the clones the settings file knows. Those are
the bridge's own clone, the companion app's, and each plugin's `cwd`. A clone
that is not on this computer is skipped. Name folders to choose them instead:
`claude-voice permissions ~/src/garage-door`. A folder that is not there is
refused and the rest go on:

```console
$ claude-voice permissions --dry-run no-such-folder
claude-voice: no-such-folder is not a folder, so it was left alone
```

## What it changes, and what it leaves alone

It writes `.claude/settings.local.json` in each repository. That file stays out
of git, so the rules are the owner's and not the project's.

- **Only adds.** The owner's own rules stay where they are, and every other
  setting in the file is kept.
- **Each rule once.** A rule the file already has is not added again.
- **A backup, once.** Before the first change it keeps the old file as
  `settings.local.json.before-quality-rules`, and never overwrites that copy.
- **A second run writes nothing.** When every rule is there already, the file is
  not touched.
- **`--dry-run` writes nothing,** not even a missing file.
- **A file that is not JSON is left alone,** and the command says so.

A missing file is created with the rules alone. To undo the rules, put the
backup back, or remove the rules from the file by hand.

## The groups

**Allowed, so a session does not ask:**

| Group | What it is for |
| --- | --- |
| read files and folders | `cat`, `head`, `tail`, `sed -n`, `grep`, `ls` and `wc`, to look at code |
| git, looking only | status, diffs, logs, branches and worktrees, read but not changed |
| git, making work on a branch | adding, committing, new branches and worktrees, cherry-picks; nothing that pushes |
| GitHub, looking only | reading pull requests, their checks and workflow runs |
| the gates of the Python repositories | `uv sync`, ruff, mypy, pytest, the mutation run and the ratchet |
| the gates of the companion app | `npm ci`, its tests, lint and build |
| the gates of the Android app | Gradle's assemble, check, lint and test |

Pushing, merging, opening a pull request and installing anything else still ask.

**Refused, even when the owner would say yes:**

- **History that cannot be got back.** A hard reset, discarding changes with
  `checkout --`, `git clean -f`, deleting or moving a branch, and forcing a
  worktree away. Each one can lose work no commit holds.
- **Secrets.** Each command allowed to read a file is refused for each secret
  file by its path. The table lists the readers and the files once, and the
  bridge writes every pair as a rule. The files are:

| File | Whose secret |
| --- | --- |
| `~/.config/claude-voice/env` | the bridge's login secret |
| `~/.config/claude-voice/machines/` | the hub's keys for the other machines |
| `~/.config/dreame…` | the robot vacuum plugin's cloud sign-in |
| `~/.config/tv-mcp…` | the TV plugin's keys |
| `secrets.yaml` | the garage door's firmware secrets |
| `~/.config/gh/hosts.yml` | the GitHub command line's sign-in |
| `~/.claude/.credentials.json` | Claude Code's own sign-in |

The secret rules guard the usual way of reading a file, not every way. A search
over a whole folder that holds a secret is not refused by them. They are a
fence against a slip, and the files themselves stay readable by the owner alone.

A new plugin that keeps a secret in a file of its own adds that file to
`secrets.files` in the table, in the same pull request as the plugin.
