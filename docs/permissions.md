# Permission rules for the voice repositories

A Claude Code session asks before every command it has no rule for. In the
bridge's repositories that means a question for each `git log` and each test
run, and from a phone each one is a tap. These rules let a session look and
test without asking. They make it ask before anything others would see or that
moves something in the house, and they refuse what must never happen without
the owner.

They live in [`src/claude_voice/permissions.json`](../src/claude_voice/permissions.json),
in named groups, so they ship with the bridge. One command adds them to each
repository:

<!-- not run: changes the settings of the reader's own clones -->
```
claude-voice permissions --dry-run      # say what it would add
claude-voice permissions                # add it
```

With no folders named it uses the clones the settings file knows. Those are
the bridge's own clone, the companion app's, the phone app's
(`claude-voice-mobile`, next to the bridge's clone unless `[paths]
claude_voice_mobile` names another), and each plugin's `cwd`. A clone that is
not on this computer is skipped. Name folders to choose them instead:
`claude-voice permissions ~/src/garage-door`. A folder that is not there is
refused and the rest go on:

```console
$ claude-voice permissions --dry-run no-such-folder
claude-voice: no-such-folder is not a folder, so it was left alone
```

## Accepting a repository's rights

A session's rights come from its repository's `.claude/settings.json` and
`.claude/settings.local.json`. A session can write those files too, so a change
on disk is not a change the owner wanted. The owner accepts a version by typing:

<!-- not run: it waits for a name typed at a terminal -->
```sh
claude-voice permissions --accept ~/src/garage-door
```

It shows each file's changes since the version last accepted. The lines that
widen a session's rights come first, marked `WIDENS`: a new allow rule, a removed
ask or deny rule, or any other changed setting, such as a hook. A file never
accepted before is shown whole, as widening. Then it asks for the repository's
name, read from the terminal itself, so a pipe cannot answer.

Several repositories can be accepted at once: name them all, or use `--all` for
every repository the org chart places in the bridge's own programme. It prints one
line per repository, with how many lines widen and how many narrow. Then it prints
every widening line in full, and asks once: type `accept <n>`, where n is the number
of repositories. A repository that only narrows gets its one line.

Only the files it showed are recorded, and only as they were shown. A file that
changes before the name is typed refuses the accept; run it again and read it
anew. The record is `accepted.json` in the bridge's state folder, with a private
copy of each accepted file beside it, so the next accept is compared with the
real contents.

The shipped rules refuse the usual ways a session would run it. That is not a wall:
a session can still call the same code another way, or type into a terminal it
started itself. Until the bridge runs under a system user of its own, the record is
the owner's file, and a session that can write it could forge it. A forged record can
only restart sessions sooner; it never widens what is on disk.

### After an update

An update brings the shipped rules to the repositories by itself, once the new
bridge answers. For each repository it works out what `claude-voice permissions`
would write, and compares that with the file on disk:

- **A change that only takes rights away is written,** and recorded as accepted
  from the shipped rules: a new deny or ask rule, or a removed allow rule.
- **Everything else waits for the owner's accept,** and the update's log says so,
  with the command to run:
  - a change that widens rights, or any change outside the rule lists;
  - a broad new rule, marked `BROAD`, such as `Bash(*)`, a whole tool like `Edit`,
    or a pattern that starts with `*`, which could lock a session out of its own work;
  - a file the owner never accepted, or has edited since, even while the new
    rules are being written: only the exact bytes that were checked are replaced;
  - the manager's own repository, so a bad rule never cuts off the session that
    relays the owner's answers.
- **Only the bridge's own programme is touched.** A folder is written only when
  the org chart places it in the programme whose file is in the bridge's own clone.
  A folder in another programme or in none, or a chart that cannot be read, is
  left exactly as it is.

To run the same step by hand: `claude-voice permissions --shipped`. It records
the commit the bridge runs from.

Rolling sessions onto accepted rights builds on this record.

## What it changes, and what it leaves alone

It writes `.claude/settings.local.json` in each repository. That file stays out
of git, so the rules are the owner's and not the project's.

- **Adds, and takes away only a retired rule.** It writes three lists,
  `permissions.allow`, `permissions.ask` and `permissions.deny`. The owner's own
  rules stay where they are, and every other setting in the file is kept. The
  one exception is a short list of retired rules in the table, under
  `retired.allow` and `retired.ask`. An older table wrote them, and they are
  gone now. Each is removed only from its own list, and only where the file has
  exactly that text; a rule that merely looks like it stays.
- **Each rule once.** A rule the file already has is not added again.
- **A backup before each change, outside the repository.** It copies the old
  file to
  `~/.local/state/claude-voice/settings-backups/<repository>/settings.local.json.<time>`
  and says where. It never overwrites an older backup. A copy inside the
  repository would make its checkout dirty, and `claude-voice plugins approve`
  refuses a plugin whose checkout is dirty.
- **A second run writes nothing.** When every rule is there already, the file is
  not touched.
- **`--dry-run` writes nothing,** not even a missing file.
- **A file that is not JSON is left alone,** and the command says so.

A missing file is created with the rules alone. To undo the rules, put the
backup back, or remove the rules from the file by hand.

The dry run says what it would remove as well as what it would add:

<!-- not run: the counts depend on the reader's own settings -->
```
~/src/claude-voice: would add 21 allow, 83 ask and 30 deny rules, and remove 2 retired allow rules
```

The retired rules so far are all allow rules:

- `Bash(cp * /tmp/*)`. The first installer allowed it, and a copy into `/tmp`
  got round the secret rules. So every computer that ran that installer kept a
  way round them until this removed it.
- `Bash(git commit -m *)` and `Bash(git commit -F *)`. One rule,
  `Bash(git commit *)`, took their place, so a commit with `-q` or `--amend`
  no longer asks.

## The groups

**Allowed, so a session does not ask:**

| Group | What it is for |
| --- | --- |
| read files and folders | `cat`, `head`, `tail`, `sed -n`, `grep`, `ls` and `wc`, to look at code |
| git, looking only | status, diffs, logs, branches and worktrees, read but not changed |
| git, making work on a branch | adding and committing, also with `git -C`; new branches, switching branch, worktrees, cherry-picks; `git rebase origin/main` and `--continue` |
| bringing main up to date, by fast-forward only | `git fetch origin`, with or without `main`; `git pull --ff-only`, with or without `origin main`; and `git merge --ff-only origin/main` |
| GitHub, looking only | reading pull requests, their checks and status, workflow runs and watching one, issues, the repository and its releases |
| pushing a session's own pull-request branch | `git push origin <branch>`, with `-u` or `-q -u`; `git push --force-with-lease origin <branch>`, with or without `-u`; and `gh pr update-branch <n> --rebase` |
| the gates of the Python repositories | `uv sync`, ruff, mypy and pytest, also with `uv run --quiet`, the mutation run and the ratchet |
| the gates of the companion app | `npm ci`, its tests, lint, typecheck and build |
| the gates of the Android app | Gradle's assemble, check, lint and test |
| editing files in the repository | `Edit(./**)`, every file under the repository. Claude Code checks a file write against `Edit` rules, so there is no `Write` rule |

Merging, opening a pull request, `gh api` and installing anything else still
ask, as does anything no rule names. Merging gets no rule of either kind on
purpose: the owner lets a session merge its own pull request once every check
is green.

The owner decided that a session may bring its local `main` up to date with
GitHub. It does so by fast-forward only, which adds GitHub's commits and changes
none. Moving `main` by force, with `git branch -f` or `git reset --hard`, stays
refused, and so does every push to it.

**Asked, even where an allow rule would let it through.** An ask rule wins over
an allow rule, and a deny rule wins over both. The question reaches the phone
like any other.

| Group | What it is for |
| --- | --- |
| pushes that delete a branch or could land on main without naming it | `--delete`, `-d`, `origin :feature`, pushing `HEAD`, `--all`, and a push naming no branch |
| a lease push in any other form than the one allowed | `--force-with-lease` after the remote, as in `git push origin feature --force-with-lease` |
| GitHub, writing what others read | commenting on, closing or reviewing a pull request; creating or commenting on an issue; creating or editing a release; editing the repository; secrets; `gh api graphql`; and `gh api` with `-X`, `--method`, `-f`, `-F`, `--field`, `--raw-field` or `--input` |
| deleting files | `rm -f`; a recursive remove is asked by the session hook, below |
| changing the session's own settings | `Edit(./.claude/**)`, so a session cannot edit its own rules without the owner |
| the devices in the house | the tools that open the garage door, switch a TV on or off, or start, stop, pause, send home, find or clean with the vacuum |
| the garage door's own server, run in its repository | the same garage tools, by the name the garage door's repository gives its own server |
| sending mail | Gmail's send, reply and forward |

The device tools are listed once, under `device tools`, with the names a session
sees the bridge under. Added with `claude mcp add` it is `claude-voice`, so a
tool is `mcp__claude-voice__garage_request`. As a claude.ai connector it is
`claude_ai_claude-voice`, so the same tool is
`mcp__claude_ai_claude-voice__garage_request`. The bridge writes each pair as a
rule. Looking at a device, such as `garage_status` or `vacuum_where`, does not
ask. A new plugin whose tools move something adds them to `device tools.tools`
in the same pull request as the plugin.

### Forced pushes to a session's own branch

The owner decided that a session may rebase its own pull-request branch and push
it again. It pushes with `--force-with-lease` only. That form refuses to
overwrite a commit the session has not seen, so another person's push is not
lost. The rules cannot tell whose branch it is. Only touching its own branches
is the session's duty, set in [AGENTS.md](../AGENTS.md).

GitHub's branch protection on `main` is the real guard. It refuses every force
push to `main`, for administrators too. These rules are the second lock, for a
clone whose remote has no such protection. They refuse a forced push that names
`main` or `master` in any of these ways:

- the branch alone: `origin main`, `-u origin main`, or `origin feature main`;
- a refspec ending in it: `HEAD:main`, `feature:main`, `feature:refs/heads/main`;
- a forced refspec: `+main`;
- the lease itself naming it: `--force-with-lease=main` or `--force-with-lease=refs/heads/main`.

They match wherever the lease stands in the command, so
`git push origin --force-with-lease main` is refused too.

The table lists the branches once and these forms once. The bridge writes each
pair as a rule, the way it writes the secret rules. A push that is not forced
but names `main` or `master` is refused too, from the forms under `other pushes`.

**Refused, even when the owner would say yes:**

- **History that cannot be got back.** A hard reset, discarding changes with
  `checkout --`, `git clean -f`, deleting or moving a branch, forcing a
  worktree away, and a `git switch` with `-f`, `--force`, `--discard-changes`
  or `-C`. Each one can lose work no commit holds.
- **Forced pushes that check nothing, or could land anywhere.** Plain `--force`
  and `-f`, anywhere in a push, also run together with `-u`, `-q` or `-v`. A
  forced refspec such as `+feature`, and `--mirror`. A lease push that names no
  branch or only `HEAD`, since that could be `main`. A lease push that deletes
  a branch, or pushes every branch with `--all`. The plain pushes the table
  allows would let all of these through without these rules.
- **Approving a pull request,** with `gh pr review --approve` or `-a`, or with
  any `gh api` call that names `APPROVE`. A session acts on GitHub as the owner,
  so its approval would show publicly in his name. Commenting on a review, or
  asking for changes, is asked instead. An approval sent from a file with
  `gh api --input` cannot be seen in the command, so it is only asked.
- **Removing what cannot be got back,** with `rm -r`, `-R`, `-fr` or `--recursive`: the
  root, the home folder by any spelling, the repository itself or `.`, any `.git`, and
  Claude Code's, the bridge's and the settings folders under home. The table lists the
  removers and the targets once, and the bridge writes every pair. The home folder and
  the repository are written as placeholders, `{home}` and `{folder}`, filled in for
  each repository when the rules are written. Every other recursive remove asks.
- **A forced push to `main` or `master`,** in every form listed above.
- **Any other push to `main` or `master`.** That is a push naming the branch
  alone, `HEAD:main`, `feature:refs/heads/main` or `+main`. A change reaches
  `main` through a pull request only.
- **Secrets.** Each command allowed to read a file is refused for each secret
  file by its path. So are the commands that copy a file, `cp` and `rsync`,
  and those that print its bytes in another form, `base64`, `xxd` and `od`.
  The table lists these readers and the files once, and the bridge writes every
  pair as a rule. The files are:

| File | Whose secret |
| --- | --- |
| `~/.config/claude-voice/env` | the bridge's login secret |
| `~/.config/claude-voice/machines/` | the hub's keys for the other machines |
| `~/.config/dreame…` | the robot vacuum plugin's cloud sign-in |
| `~/.config/tv-mcp…` | the TV plugin's keys |
| `secrets.yaml` | the garage door's firmware secrets |
| `~/.config/gh/hosts.yml` | the GitHub command line's sign-in |
| `~/.claude/.credentials.json` | Claude Code's own sign-in |

- **Settings and secrets, read broadly with `sed`.** `sed -n` is refused for
  anything under a `.config` folder, and for any path that says `secrets`. These
  two are wider than the pairs above, and were in the first rules the owner
  installed. A test keeps every rule refused then refused now, so moving the
  rules into the bridge took nothing away.

The secret rules guard the usual way of reading a file, not every way. A search
over a whole folder that holds a secret is not refused by them. They are a
fence against a slip, and the files themselves stay readable by the owner alone.

A new plugin that keeps a secret in a file of its own adds that file to
`secrets.files` in the table, in the same pull request as the plugin.

## The session hook

Two routine steps used to wait on the owner many times a day: clearing out a
session's own scratch folder, and filing an issue. The owner decided that a
session may take both alone, within narrow limits. A rule pattern cannot hold
those limits, so a small hook does. Claude Code runs it before every shell
command, and the bridge writes it beside the rules.

It lets through:

- **a recursive remove inside the session's own scratchpad.** It must be a plain
  `rm` with no quoting, chain, glob or variable. Every target, with links and
  `..` followed, must lie inside the scratchpad of the session that runs it.
  Another session's scratchpad does not count.
- **an issue in the programme's own repositories.** The repository must be named
  with `--repo`, and listed as a component's remote in the org chart. These
  repositories are public, so whatever the issue holds is published at once.

So an issue asks when:

- its command names a variable or a home folder (`$`, `~`), which the shell would
  fill in after the hook has looked;
- its body file is neither in the session's own scratchpad nor a file git tracks.
  An ignored file, such as one holding a network password, never qualifies;
- its title, body or body file holds the shape of a secret: a token prefix such as
  `ghp_`, a private-key header, a long run of hex or base64, or a secret's name set
  to a value. A 40-character hex run is a commit, and allowed;
- it names one of the owner's personal terms. The bridge passes the hook a terms
  file from its own settings, `personal_terms_file`. Only a count of matches is
  read, never the words. With no terms file, every issue asks.

Every other recursive remove it asks about, however it is spelled: in quotes,
behind a backslash, `command` or `env`, by a full path, as `find -delete` or
`-exec rm`, or as a tree removal inside another program. A single-file `rm` and
`git rm` keep the rules they always had, but only with a plain command word and
plain flags. A command whose name the shell builds, from a glob or `$'…'`, is not
caught, as it was not before; the deny rules above stay the floor for what cannot
be got back.

A settings ask beats a hook's allow, so the old asks `Bash(rm -r*)` and
`Bash(gh issue create*)` are retired; the hook asks in their place. The hook runs with
the system `python3` and the standard library only, so a broken bridge
environment cannot stop it. If it cannot start at all, a recursive remove inside
a repository would run unasked. So `claude-voice permissions --check` runs each
repository's hook once, and the bridge's health shows a hook that does not
answer as an error, with the fix.
