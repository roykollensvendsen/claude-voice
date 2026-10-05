---
name: write-commit
description: Read the changes and write the message that explains them to whoever reads it next, then commit once the user has approved it. Use this for every commit without exception, including fixups, amends and rewords, for every pull request body, and whenever the user says "commit this", "let's commit", "open a PR" or is wrapping up a piece of work. Use it even for a one-line change, because the type, the mood and whether the change is one coherent thing are all decided here and a linter can only tell you afterwards that the shape was wrong.
---

# Writing a commit message

A green linter says the message is well-formed. It never says the message is
true, that the type is the right one, or that the change is one thing. Those
are what this is for.

## Who reads this, and what did they come for

The test is not whether the message looks like a person wrote it. It is
whether it was written **for** a person. A terse list passes if it answers
what its reader came for. A warm paragraph fails if it does not.

**Write for a reader who has never seen this project.** That is the standing
assumption here, not a courtesy for the occasional outsider. They do not know
what the files are called, what the settings do, or what any abbreviation in
this repository stands for, and they should not have to open the code to follow
the message. If a sentence only makes sense to somebody who already knows the
tree, it is not finished.

Three readers, and none of them wants the same thing:

- **The reviewer, now.** What changed, whether it is sound, what to look at
  hardest.
- **Someone months from now**, arriving from `git blame` at a line they do not
  understand. Why it is this way, and what else was considered.
- **You, reconstructing.** The same as the second, and you will have forgotten
  more than you expect.

Nobody came to find out that the tests passed. Take every line and ask who it
is for. If the answer is nobody, cut it. What the conventions rest on, and which parts are
convention rather than evidence, is
the `start-repo` skill's `references/commit-messages.md`.

## Do it in this order

1. **Read what changed.** `git status` and `git diff`, staged and unstaged.
   Write the message from the diff, not from memory of what you intended.
2. **Check for mixed concerns.** If the change covers independent things, a
   fix and a feature, or a shared component and its caller, propose the
   split, list the groupings, and let the user decide before going on. Git's
   own guidance asks for one logical change per commit, because a commit that
   does two things can be reverted for neither.
3. **Read the recent history.** `git log --oneline -10` shows the house style,
   which scopes exist, and how much body a change of this size usually earns.
4. **Read the lint configuration.** `committed.toml` at the root is what CI
   applies. Validate the draft against it rather than against this page, so
   the two cannot drift.
5. **Draft, present, and wait.** Show the message and wait for approval.
   Never commit unasked.
6. **Stage by name.** Never `git add -A` or `git add .`; stage the files the
   message describes.

## The subject

```
<type>(<scope>): <Subject>
```

**Imperative mood.** The subject completes "if applied, this commit will ...".
So "Add the check", never "Added", "Adds" or "Adding", and never a noun phrase
like "A new check" or "Apache-2.0, a guide and a template". That test is Chris
Beams' from 2014; git's own guidance asks for the mood without offering a test
for it.

**Capitalised, no full stop, at most 72 characters** up to the last word. The
linter measures the subject without its final word, so a long last word is
free; that is its own design, read in its source rather than its
documentation. Git's own documentation suggests around 50 and has since 2008,
before the blog post usually credited for it. Neither GitHub nor gitk truncates at any character count:
both clip by the width of the column, so the number is about reading, not
about tooling.

**A specific verb beats a vague one.** "Refuse", "Pin", "Split", "Narrow",
"Guard" say what happened; "Update", "Change", "Improve" could describe
anything. This is guidance and not a rule: no published source names verbs to
avoid, and no linter can tell vague from specific without refusing good
subjects, so it is a judgement made here rather than a gate applied later.

## The body

Separate it from the subject with a blank line. Without it git reads the whole
message as one long subject and the body disappears from everything that
prints `%b`.

**Give the reader ground to stand on first.** For a small change the change
itself is the ground, and the next rule is the whole job. For anything larger,
one or two sentences first: what this part is for, and where it stood. A
message that opens on the change assumes a map the reader does not have, and
after a few months that reader includes you.

**Use everyday words, and explain a term before you lean on it.** Write it the
way you would say it to somebody over coffee who does not work on this. A file
name, a setting, a flag or an abbreviation is jargon until the sentence before
it has said what it is — "a decision record, which is a short page saying why a
choice was made" — and after that it can be used freely. Never open on jargon.
"ADR-VI-015 made a pull request the only way onto main" tells a stranger
nothing; "until today anyone could push a change straight to the main branch,
with nothing checking it first" tells them everything and costs four more
words.

**Push the mechanical detail down.** Exact file paths, setting names, command
flags and record numbers belong at the bottom, under a heading of their own —
"The details, for whoever maintains this" — not threaded through the
explanation. A reader who wants them will scroll. A reader who does not should
be able to stop after the first two paragraphs and understand what happened and
why.

**Then say what changed, in the words you would use out loud.** One sentence,
before any reasoning, saying what is different now. That sentence is what a
reader asks for when a message leaves them guessing, so write it rather than
making them ask. Then, for anything not self-explanatory:

1. **Why.** What was wrong, or what was needed.
2. **What it costs.** Where the change gives something up, say so.
3. **Anything surprising** the next reader would otherwise trip on.

Explain why, not what: the diff already shows what.

**Leave out what is recorded elsewhere.** Four things turn up in bodies and
belong in none of them:

- That the gates passed. Continuous integration reports it on the same page,
  and repeating it is the copy that pointing rather than copying forbids.
- That you checked something. A check either runs and is recorded, or it does
  not exist.
- Counts of your own process: files touched, lines added, sentences you
  rewrote. The diff holds all of it.
- Anything the diff shows plainly.

**Write it to be read, not to be complete.** Short sentences: under twenty
words on average and none over thirty, which is the rule every page in these
repositories lives under and which commit messages escaped for too long. Aim
at about eighty words. Longer is fine when the change earns it, and the
earning should be obvious. Keep lines under 100 characters, which is what the
linter enforces; the often-quoted 72 has no first-party source anywhere.

**Example.** Not this:

```
The linter has an imperative_subject setting and, as of 1.1.11, flags
nothing: not Fixed, not Fixes, not Fixing. Turning it on alone would have
been a rule with no mechanism, which is the failure this project argues
against.
```

This:

```
A commit subject must now start with a verb. "Add the check" passes;
"Added", "Adds" and "A new check" are refused.

The linter has a setting for this, but it never fires: it only looks at
lowercase words, and we require a capital. So a small script does it
instead.
```

**A body makes claims, so check them before writing them.** "This is a pure
rename", "the output is unchanged", "it settles within two seconds" are each
falsifiable in one command, and a wrong one outlives the review in `git log`
where nothing re-examines it. These are invented at message-writing time, so
nothing earlier in the work has checked them.

## How much body

- **None.** Version bumps, formatting, a typo, anything the subject already
  explains.
- **A sentence or two.** A simple fix or feature where a little context helps.
- **Problem and solution in full.** Any change where the reason for the
  approach is not obvious from the diff.

Precision beats brevity. Use the words the content needs and no more, and do
not restate one point in three forms.

## The pull request body

The same message at a larger size, for the same readers. It carries the one
thing no single commit can: why the whole set exists together, and what a
reviewer should look at hardest.

Five parts, in this order:

1. **The ground.** What this part of the system is for, and where it stood
   before. Written so somebody who was never in the conversation can read the
   rest, in plain words, with no file name or abbreviation that has not been
   explained first.
2. **What changed**, and why it was done this way rather than another way.
3. **What a reviewer should push on.** Name the weakest parts of your own
   change. This is the section that earns the review: a reviewer who has to
   hunt for the soft spots finds fewer of them.
4. **The details, for whoever maintains this.** Everything mechanical, gathered
   in one place where it can be skipped: which files, which settings, which
   records, which flags. Nothing above this heading needs it to make sense.
5. **The checklist the template carries**, answered honestly. It is there for
   the rules no script can measure, so a ticked box that is not true is worse
   than an empty repository.

Do not restate what continuous integration reports on the same page. Do not
list the commands you ran.

## Examples

A trivial change, subject only:

```
chore: Pin the workflow's actions to a commit
```

A fix with the reason it was made:

```
fix: Compare a gate's target only where the workflow runs it as a command

One of our automated tests checks that every quality check the guide
promises is really run when a change is proposed. It compared the check's
name and the thing it is pointed at, which was right for some checks and
wrong for the one that lints commit messages: that one is not run as a
command, so the thing it is pointed at never appears, and the test failed
on a repository that was doing nothing wrong.

It now compares the target only where there is one to compare. The
mismatch it was written to catch is still caught.
```

The same change, written the way this page refuses:

```
fix: Compare a gate's target only where the workflow runs it as a command

The stronger comparison was right about mypy and wrong about committed,
which checks.yml reaches through a `uses:` rather than a `run:`, so its
target can never appear in run_steps.
```

Every fact in the second version is true, it is a third the length, and a
person who does not already know this tree learns nothing from it.

## Rewriting a message that is already pushed

An amend or a reword gives the commit a new identity, and every commit after
it too, so the branch has to be replaced rather than added to. Three things
bite here, each of which has cost real work:

- **Rewrite the branch, not every reference.** `git filter-branch -- --all`
  rewrites the backup branch you just made and the remote-tracking ref along
  with it, leaving you with no recovery point and a wrong idea of what the
  remote holds. Name the branch instead; `refs/original/` holds the originals
  if you forget.
- **Ask for `HEAD`, not `root..HEAD`.** A range excludes its own start, so
  checking "the whole history" that way skips the first commit.
- **Never `git reset --hard` to undo a test commit.** It discards tracked
  changes you have not committed, including work in files you were not
  thinking about.

Check the result before offering the push: the tree at the rewritten head must
be identical to what it replaces, and only the messages may differ.

## Never

- Commit before the user has approved the message.
- Amend a commit unless the user asks.
- Skip the hooks with `--no-verify`.
- Add an attribution trailer the project has not asked for.
- Use an emoji, in the subject or the body.
- Pass the message any way but a heredoc, so quoting cannot mangle it.
