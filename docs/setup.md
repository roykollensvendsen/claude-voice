# Setting up the platform, and profiles

`claude-voice setup --check` lists every step of setting up claude-voice on this
computer, in the order a person takes them. It says, for each one, whether it is
done, missing, or manual: a step only you can take, such as signing in. It only
reads, so it can be run at any time and changes nothing. Supported today: Linux
with systemd.

Only the check is built so far. Without `--check` the command says so:

```console
$ claude-voice setup
claude-voice: only the check is built so far. Run `claude-voice setup --check`: it says what is left, and changes nothing.
```

## The steps

| Step | Done when | When it is not |
| --- | --- | --- |
| `uv`, `node`, `claude_code`, `bridge` | the program is installed | missing, with how to install it |
| `config` | `~/.config/claude-voice/config.toml` exists | missing |
| `bridge_check` | `claude-voice check` passes, apart from signing in | missing, with that check's own fix |
| `signed_in` | Claude Code is signed in | manual: `claude`, then `/login` |
| `service` | the bridge is installed as a user service | missing: `deploy/install.sh` |
| `ui_build` | the companion app is built | missing |
| `phone_route` | a public address is set in the environment | manual: Tailscale and Funnel are yours |
| `repo:<name>` | each repository in the profile is cloned | missing, with the `git clone` line |
| `hooks:<name>` | a clone with a `.githooks` folder has its commit-msg hook on | missing, with the `git config` line |
| `plugin:<name>` | each plugin in the profile is set up and approved | missing (its block to add), or manual (`claude-voice plugins approve <name>`) |

Setup never opens the service's env file, `~/.config/claude-voice/env`, because
it holds the login secret. So a public address set only there shows as manual,
and the step names the key to look for in that file yourself. `claude-voice
check` still reads the file for its own warnings.

A step marked optional, such as the phone app's repository, never counts as
missing. The check exits 1 while anything else is missing. `--json` gives the
same steps, plus `ready` and the names still `missing`, for an assistant doing
the setup.

## Profiles

A profile says which repositories and plugins a developer gets. It is a TOML
file of `[[repo]]` entries (a `url`, the `name` of its folder, a `kind` of core,
app or plugin, and whether it is `optional`), `[[plugin]]` entries (a `name`, the
`repo` it comes from, its `[plugins.<name>]` `block` as a template, and its
`config` files as templates), and an optional `remove = [names]`.

The layers, each laid over the one before it:

1. **The default profile**, shipped with claude-voice in
   `src/claude_voice/default_profile.toml`. It names the bridge, the companion
   app and the phone app, and no plugin.
2. **Your own**, if you name one in config.toml: `profile = "~/.config/claude-voice/profile.toml"`.
3. **This machine's**, `~/.config/claude-voice/profile.local.toml`, if it exists.

A later layer adds an entry, or replaces one by its name. `remove` drops an entry
by name, so you need no full copy to leave one plugin out.

### Nothing of your own in a profile

A profile is shared as a plain file, so what it may hold is an allow-list, not a
search for known secrets (`tests/test_profiles.py`):

- **A repository** is an `https://github.com/<owner>/<name>` address, named as
  its address names it. The project's own account may appear only in its three
  repositories: the bridge, the companion app and the phone app.
- **Every word** in a template, and in a plugin's name, is a `{placeholder}`, a
  word from the short list in `profiles.SAFE_WORDS`, or the name of one of the
  profile's own repositories. That covers keys, table names, strings and command
  arrays. So `"--host", "garage.local"`, a `[stue]` table, or a plugin named after
  a home device are refused.
- **Every number** is one of the documented defaults in `profiles.NUMBERS`.
  Coordinates, ports and IDs are placeholders.
- **A comment starts its line**, and the comments are scanned for anything that
  looks private: an address, a coordinate, an email, a host on a home network, a
  long number or a key. A TOML parser drops comments, so this is their only check.
- **A plugin's block** sets only its own `[plugins.<name>]`, and its config files
  go under `~/.config/`.

These are checked one file at a time. Whether each plugin's repository is listed
is checked on the merged profile instead. A plugin in your profile may come from
a repository the default lists, and a later layer that removes a repository a
plugin still needs is refused, naming every layer read.

Your values, such as your home's position for the weather, live only in your own
`~/.config/claude-voice/values.toml`, never in a profile.

## Where each repository stands

`claude-voice repos sync --check` reads the merged profile and says, for each
repository, where its folder is and what a sync would do. The folder is the one
`[paths]` names, the plugin folder a `[plugins.<name>]` block runs in, or else
one under `CLAUDE_VOICE_ROOT` (`~/src` by default).

It reads local refs only. It never fetches, so "behind" is as of the last
fetch, and it takes none of git's optional locks, so not even git's index is
written. The sync itself is not built yet:

```console
$ claude-voice repos sync
claude-voice: only the check is built so far. Run `claude-voice repos sync --check`: it says what is left, and changes nothing.
```

A sync will clone a missing folder, and fast-forward a clone from its own
upstream only when nothing in it can be lost. Each of these is left alone, and
the check says which and why:

- a folder that exists and is not a clone;
- a clone whose origin is not the profile's, such as a fork;
- a detached HEAD;
- a rebase, merge, cherry-pick, revert or bisect in progress;
- uncommitted or untracked files;
- a branch other than the default;
- a branch that has diverged from its upstream.

## How read-only is proven

`tests/test_setup.py` runs both checks in a temporary home holding a clone with
a stale index, a folder that is not a clone and a settings file. Every file's
path, mode and contents must be the same afterwards. The clean-install job runs
`setup --check` on an empty machine, where it must report exactly the steps
expected there, and compares the home folder before and after it.
