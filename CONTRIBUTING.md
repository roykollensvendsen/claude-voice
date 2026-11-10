# Contributing

Every rule this repository states is enforced by something that can fail. If
you find a rule here with no mechanism behind it, that is a bug worth
reporting.

## Set up

<!-- not run: clones the repository the reader is already in -->
```
git clone https://github.com/roykollensvendsen/claude-voice
cd claude-voice
uv sync --all-extras
git config core.hooksPath .githooks
uv run claude-voice permissions
```

The `git config` line turns on the commit-msg hook, which refuses a message the gates
would refuse later while it is still being written. The last gives Claude Code
the permission rules for this clone and the other voice repositories. It may
look and run the tests without asking, must ask before a push to main, a
GitHub write others see or a device tool, and may never delete history or read
a secret. [docs/permissions.md](docs/permissions.md) lists them; `--dry-run` shows
what it would add first.

The commit linter is a separate binary,
[committed](https://github.com/crate-ci/committed).

## The gates

All of these run in CI on every pull request. Run them before you push,
because they are the same lines:

<!-- not run: these are the gates; running them from inside a gate would recurse -->
```
uv run ruff check . && uv run ruff format --check .
uv run mypy src
uv run pytest -q --cov
committed --no-merge-commit origin/main..HEAD
uv run python scripts/ratchet.py check --base origin/main
python3 scripts/clean_install.py
```

The last one follows the README's "Start here" on an empty Ubuntu, so it needs
Docker and about two minutes. Run it when you touch the README's first steps,
the setup check or the packaging.

The ratchet takes about ten minutes, because it switches every rule off in
turn; `--skip-mutation` gives a quick look that keeps main's mutation figures.

Run them after the last edit, not before it. A file edited after the format
gate ran will fail in continuous integration, which is the cheapest possible
way to learn that the gates are not a ritual performed once.

## The quality ratchet

The gates above pass or fail. None of them notices the code slowly getting
worse, so the ratchet compares these levels with the ones main has reached:

| Measure | Better is |
| --- | --- |
| `coverage`: the share of the source the suite runs, in percent | higher (it may wobble by half a point between machines) |
| `mutation`: of the rules marked in the source, how many turn their own test red when switched off | higher |
| `tests`: how many tests pass | higher |
| `lint_errors`, `type_errors` | lower |
| `cycles`: groups of modules that import each other in a circle when they load | lower |
| `layer_violations`: imports, made when a module loads, that reach a module in the same or a higher layer (`LAYERS` in `scripts/ratchet.py`), plus modules given no layer | lower |
| `duplication`: the share of non-blank source lines, in percent, in a block of six or more lines that appears more than once, ignoring indentation | lower |
| `complexity`: per module, the McCabe count of its most tangled function, not counting functions defined inside it; a new module keeps every function at 15 or below | lower |
| `test_seconds`: how long the suite takes, against `test_budget` | inside the budget plus its tolerance |

The layers, from the bottom: foundations (`procs`, `config`, `store`, `metrics`,
`titles`); parts, each about one thing; parts built from parts (`events`,
`tree`, `approvals`); the doors in from outside (`api`, `phone_hook`); `server`,
where it is all put together; and `cli`. A new module is given its layer in
`LAYERS`. When code is needed twice, it goes into a function both places call,
rather than being copied.

`scripts/ratchet.py check` fails a change that makes any of them worse than
main. Main's levels are measured by continuous integration after every merge
and kept with that run, so a pull request never records them, and merging one
never makes another stale ([ADR-CV-009](decisions/ADR-CV-009-main-keeps-the-quality-levels.md)).
When main's levels for that commit are missing, the check measures the commit
itself, which takes about ten minutes more. A change that must make a measure
worse says so in its pull request, on a line of its own:

<!-- not run: an example line in a pull request -->
```
Ratchet-Exception: coverage - the module it covered was deleted
```

The time budget is a limit, not a level: it is the one thing in `quality.json`,
and it changes only by hand.

## How a change is made

1. **Decide before you build.** A choice that costs more to reverse than to
   take is written as a record under `decisions/` before the code, in the form
   [`decisions/README.md`](decisions/README.md) gives.
2. **Say it before you build it.** A rule, a flag or a field is written down
   before it is code. If the documentation does not say it, it is not decided.
3. **Write a test and watch it fail.** A test that passes on its first run
   proves nothing. Where the rule already exists and no test can precede it,
   turn the rule off with `uv run python scripts/mutate.py`, watch a named test
   go red, and keep the row in `scripts/mutations.toml`.

   A rule is a line marked `# RULE: what it says` in the source. Every one needs
   a row in `scripts/mutations.toml` and a test whose name is the rule's own
   words — `# RULE: a spoken reply is capped` wants
   `test_a_spoken_reply_is_capped`. `tests/test_rules_have_evidence.py` refuses
   a rule missing either. That naming is not decoration: the mutation script
   checks that the test named after a rule is one of the ones that went red, and
   fails the run when something else broke instead. A kill by an unrelated test
   is not evidence about the rule.

   Do not edit the tree while it runs. It refuses to restore a file that
   changed underneath it and says so, but the run it was in the middle of is
   then about a tree that no longer exists.
4. **Write the smallest code that turns it green**, then refactor with the
   suite green.
5. **Open one pull request per change**, with the template's checklist
   answered. The template is the mechanism for the rules no script can measure.

`main` is protected: a pull request is required, every status check has to pass,
force pushes and deletions are refused, history stays linear, and none of it is
waived for administrators. That is a repository setting rather than a test,
because a test that proves it would need a credential to pass and would fail for
the wrong reasons. It is the one rule here enforced by something the suite
cannot see. `python scripts/forge.py roykollensvendsen/claude-voice` in the `start-repo` skill is what
applies it, and it says plainly when the forge refuses — branch protection needs
a public repository or a paid plan.

A pull request lands by rebase, and by nothing else. Every commit on a branch
therefore arrives on `main` individually, so write each one to stand alone:
green, lintable, and readable from `git blame` without the others. A branch
cannot be tidied at the end by squashing it, which is why `committed.toml`
refuses a fixup and a work-in-progress commit rather than trusting you to clean
up later.

A fixup is still the way to correct an earlier commit on your own branch, as
long as it is folded in before you push:

<!-- not run: it needs a branch with a commit to correct, and it rewrites that branch -->
```
git commit --fixup <the commit it corrects>
git rebase --autosquash origin/main
git push --force-with-lease origin <your branch>
```

The commit-msg hook lets a `fixup!`, `amend!` or `squash!` commit through for
this, and continuous integration still refuses one that reaches a pull request.

## More rules for every change

- **A tool's first line says what it is for.** The Claude app shows an
  assistant each tool by the start of its description until the tool is
  loaded. So the first line of its description says what the tool is for, in
  one whole sentence of at most 78 characters, where the app cuts it.
  `tests/test_surface.py` checks every tool the bridge offers itself. A
  plugin's tools are not checked here yet; [`decisions/deferred.md`](decisions/deferred.md)
  says when they will be.
- **One pull request per topic.** Several small changes on one subject go
  together; changes on different subjects go apart, so each can be reviewed,
  tested and, if need be, reverted on its own.
- **Never break what Hermes or the voice app uses.** Other programs call the
  bridge's tools by name and read their fields. Add a tool or a field rather
  than renaming or removing one, and say in the pull request which callers it
  touches. A change that must break one is agreed with that caller first. The
  voice is the main caller, so once a tool changes, ask it what it needs, as
  [docs/voice-feedback.md](docs/voice-feedback.md) describes.
- **No links to a Claude session in a commit message.** A `Claude-Session:`
  line, or any `claude.ai/code/session_` address, points into one person's
  account and is a dead link to everyone else. `scripts/no_session_links.py`
  refuses it, in the commit-msg hook and in continuous integration, for new
  commits only; older history is kept as it is.
- **Credit the people whose work a change builds on.** A commit that takes in
  someone else's work keeps them as author, or names them with a
  `Co-Authored-By:` line.
- **Say why a test or a mutation row is removed or weakened.** The tests and
  the mutation rows are what stop a later change from quietly breaking a rule,
  whoever wrote it. A pull request that deletes one, loosens what it checks, or
  points a row at something easier says why in its description, so the reviewer
  decides it on purpose.
- **Ask the person who wrote the lines you change.** When a pull request
  changes code or tests a contributor outside the project wrote, mention them
  in it, so they can say if something they relied on breaks. They are asked,
  not given a veto; the maintainer decides.

## When someone else's change comes in

A contribution from outside the project is reviewed as it is. Questions and
wishes go in a comment on the pull request. Nobody pushes to the contributor's
branch, except a small fix they have agreed to, and their commits keep them as
author.

**When it lands, thank them in the pull request, and say what was good.** Name
what made it easy to review or to trust, and why it helped, so they know what
to do again. Add a tip if there is one worth having. A bare "merged, thanks"
tells them nothing they can use.

**Then learn from it.** When a contribution does something better than our own
changes do, it goes in the list below, and our own pull requests are held to it
from then on. The `review-contribution` skill walks an assistant through all
of it in order, with a sandbox for running the contributor's code.

What contributions have taught us:

- **Measure it in the real product before writing a rule about it.** The rule
  for tools' first lines rests on what the Claude app was seen to show, not on
  what its documentation says (#108).
- **A new rule comes with the check that enforces it.** A note asks to be
  remembered; a test cannot be forgotten (#108).
- **Turn a slip into a check, once.** A mutation row that broke the code was
  found by hand; now a test refuses any such row (#129).
- **Say what was not checked.** A pull request that names the part with no test
  of its own saves the reviewer from finding it (#129).

## Sending a change from your own clone

You do not need to set any of this up by hand. Ask the bridge for the
*contribute* session, by voice ("start a contribute session") or in the
companion app. It starts a Claude Code session in your clone of claude-voice
that already knows these rules. Tell it what you want changed; it makes a fork
under your GitHub account if you have none, works on a branch, runs the gates
above, and opens a pull request to this repository.

It cannot push to this repository itself: a guard refuses every push whose
target is `roykollensvendsen/claude-voice`, however the command is written.
Its folder, name and first message are set in `src/claude_voice/roles.toml`;
change them for yourself in `~/.config/claude-voice/roles.toml`.

## Documents that restate a fact

Point rather than copy. Where a document has to copy anyway, two test files
keep the copy honest. Any block fenced as ` ```console ` is run by
`tests/test_documented_commands.py`, and its output has to match what the page
shows. Pages are found by walking rather than listed, so a new one is covered
the day it is written; a block that looks like commands and is not run must
carry `<!-- not run: why -->` above it, because choosing another fence would
otherwise be an opt-out nobody sees.

`tests/test_documentation.py` checks the facts that are not commands: the
gates above against the workflow that must run them, and every deferred item
against its trigger. Add a test there whenever you write a fact into prose
that also lives in code.

## When something bites you

Write the lesson where the next person doing this task will read it, in the
same pull request as the fix. Where the mistake is checkable, make it a test or
a check rather than prose: prose gets skipped under the same pressure that
crowds out learning in the first place, and a failing test does not.

This is the one pattern with a measured effect behind it. A surgical checklist
run during the operation, rather than a lesson filed after it, halved the death
rate across eight hospitals. The incident-review literature diagnoses the
failure from the other side, naming the lesson nobody wired back to anything an
"orphan action item". The reasoning and its sources are in
[`references/learning-from-experience.md`](references/learning-from-experience.md).

Attach it to the step it belongs to, as a sentence in that step. Not a section
of its own, and never a "gotchas" heading: that is a drawer things are filed in
rather than read from. A lesson with no step to attach to does not belong in
that skill, and the absence is the signal that it belongs in another one. One
line in the skill, and the detail in `references/` if it needs more.

A lesson written into a skill keeps its provenance in `git blame`, not in the
text: point at the line, get the commit, get the story. Never write a commit
hash into a skill. It duplicates what blame already gives, it dates the
guidance, and the commit that records a lesson cannot cite itself.

What blame does not give is a corpus. It answers why one line says what it
says, and never which lessons have gone stale, which contradict each other, or
which step has none. That is what a knowledge base is for, and
[`decisions/deferred.md`](decisions/deferred.md) says what would make building
one worth it.

There is no retrospective ritual here on purpose. Every source measuring one
is about information flowing between people in a team, which has no referent
when the team is one person and their agents.

## Commit messages

**Write them for somebody who has never seen this project.** They do not know
the file names, the settings, or what any abbreviation here stands for, and a
message that sends them into the code to find out has failed. Everyday words,
and a term explained in the sentence before it is leaned on. Anything
mechanical — file paths, settings, flags, record numbers — goes at the bottom,
under a heading of its own, so that everything above it can be read without
them. The [`write-commit`](.claude/skills/write-commit/SKILL.md) skill has a
worked example of the same change written both ways.

[Conventional Commits](https://www.conventionalcommits.org/), checked by
`committed.toml`: one line, `type: Subject`, at most 72 characters,
capitalised after the type, no full stop. Types are build, chore, ci, docs,
feat, fix, perf, refactor, revert, style and test. Keep body lines at or under
100 characters. Say why the change was made, not what the diff already shows.

Write the subject in the imperative: it completes "if applied, this commit
will ...". So "Add the check", never "Added", "Adds", "Adding", and never a
noun phrase like "A new check". The linter has a setting for this, and it fires only on a lowercase first
word while the same configuration requires a capital, so it can never fire;
`scripts/imperative.py` is what refuses it.

A `commit-msg` hook runs both while you are writing the message, rather than
in CI afterwards. Turn it on once per clone:

<!-- not run: it is already on in this clone -->
```
git config core.hooksPath .githooks
```

It is a convenience and not the gate: `--no-verify` skips it, and CI is what
actually blocks.

The hook also warns when a body is harder to read than it needs to be:
sentences over thirty words, an average over twenty, a body far past eighty
words. It only warns. How long a sentence needs to be is a judgement, and a
gate that refuses a message nobody can shorten teaches people to bypass it.

## Licence

GPL-3.0-or-later, in `LICENSE`. A contribution you submit is under that licence;
there is no separate agreement to sign.
