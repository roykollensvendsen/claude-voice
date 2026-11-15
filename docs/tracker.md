# Where pushed work stands

The bridge is to read the state of pushed work from a work tracker, behind one
narrow interface. The first tracker is GitHub, and the first step only reads.
`claude-voice tracker status --check` takes one repository's open pull requests
and gives each one of three states. It writes nothing anywhere, needs no new
token, and makes no change on GitHub.

## The three states

Each open pull request gets the first state that holds, from fixed GitHub facts.
Its title and text are never used to decide.

| State | When |
| --- | --- |
| waits on owner | a review is requested from the repository's owner |
| stopped | the latest run of a check failed |
| stopped | it conflicts with the branch it goes into |
| stopped | nothing changed on it for a day |
| moving | none of the above, drafts included |

A cancelled check run is skipped, so the latest run that finished decides. A newer
run cancelled on its way then never hides an older failure. "Changed" is GitHub's
own update time, which also moves on any comment, a bot's included.
The lines are told in that order: what waits on the owner, then what has
stopped, then what is moving.

## What it prints

One line per pull request, with its number, its state and why. A summary line
comes last. Here is a made-up repository with two open pull requests:

<!-- not run: it reads a live repository on GitHub, whose pull requests change -->
```
$ claude-voice tracker status --check --repo example-owner/example-repo
#12 stopped: a check failed: tests
#14 moving: no check has failed and no conflict
example-owner/example-repo: 2 open pull requests: 1 moving, 1 stopped, 0 waiting on the owner
```

Without `--repo` it reads the repository of the current folder. A `--repo` that
is not owner/name is refused before GitHub is asked. It reads at most 100 open
pull requests, and a last line says so when it reached that many. It exits with 1
when a line was refused, GitHub did not answer, or the repository is not public.

```console
$ claude-voice tracker --help
usage: claude-voice tracker [-h] [--check] [--repo REPO] [--words WORDS]
                            {status}

positional arguments:
  {status}       status: where each open pull request stands

options:
  -h, --help     show this help message and exit
  --check        read one repository's pull requests; write nothing
  --repo REPO    owner/name (default: the repository of this folder)
  --words WORDS  the private word list (see docs/tracker.md)
```

## The privacy gate

Every line must be one a public board could show, so each goes through a gate
before it is printed:

- A repository that is not public is refused whole.
- Text shaped like a token or a key is blanked.
- A line with private data is refused. The refusal names the pull request, never
  the text.

The scan for private data is the one the friction log uses. It looks for email
addresses, exact coordinates, a charger ID, home addresses and the owner's own
private words. A private word needs four letters or more.

The owner's words are kept outside every repository, in
`~/.config/claude-voice/private.toml`, as `words = ["...", ...]`. `--words`
names another file. A missing file is said plainly, and then only the patterns
are checked. A file that is there refuses the check when it cannot be read, when
anyone but the owner may read it (on Windows, files have no such mode), or when a word in it is empty. So the scan never
runs short without anyone seeing it. The sessions' permission rules refuse them
the file, as they refuse the secrets ([permissions.md](permissions.md)).

## What comes later

The interface also names three writes: opening an item, setting its state, and
linking a pull request to it. They are not built. Each refuses for now, so this
step cannot change a tracker by mistake. The board they will write to, and the
token it needs, are the owner's decisions and come in a later step.
