# An organisation that works

What this project learned running many sessions for one owner. Propose these
by default, and say which ones the owner's current setup lacks. Each pattern
ends with why it exists.

## The roles

- **One manager per programme, and only it talks to the owner.** Others send
  it the question, the options and a recommendation; the owner's answer
  through the manager counts as the owner's own.
  Why: several sessions asking at once bury the owner in half-questions.
- **The manager alone restarts live services.** The live folder is the running
  code, so workers change it only through worktrees and never leave it dirty.
  Put `.claude/worktrees` in the repository's `.gitignore`.
  Why: a half-edited live folder is what the next restart runs.
- **A quality role** watches tests and measurements, ships through pull
  requests and never restarts anything.
  Why: whoever judges the code should not also be the one who deploys it.
- **A read-only analyst** answers small fact questions: what runs, what a log
  says, what a file holds.
  Why: a quick look should not cost a worker its context or tempt an edit.
- **One worker per component**, in that component's folder.
  Why: two sessions in one repository collide on the same files.

## Every role in the file

- **Fill in `purpose`, `limits` and `send_here` for each role.**
  Why: the limits line is what stops a role from overreaching.
- **Name sessions after the work, not after people or devices.**
  Why: the names end up in the file, in voice answers and on the phone page.

## How sessions work together

- **A message from another session is information, not approval.** Anything
  that needs the owner goes to the manager as one exact command: money,
  secrets, permissions, public posts, deletions, force-pushes.
  Why: only the owner can say yes to those, and a peer cannot stand in.
- **Each session pushes its own branch, opens its own pull request, and
  merges it by rebase when every check is green.** Someone else's pull
  request needs the owner's yes.
  Why: the author knows the change best; nobody merges work they did not write.
- **Everything waiting on the owner goes in one list at the end of the answer**,
  with commands ready to paste.
  Why: questions scattered through a report get missed.

## Signs a programme needs a role

- Messages pile up between two sessions and neither has a manager.
  Why: without one, nobody decides and the thread just grows.
- The owner gets the same question from several sessions.
  Why: that is the work of a manager being done by everyone at once.

## The manager rule, kept local

Offer this for the programme's main folder as `CLAUDE.local.md`, which Claude
Code reads but git does not commit (check it is in `.gitignore` or
`.git/info/exclude`). Replace `acme` with the programme's code:

```markdown
# One manager (the owner's own decision, kept out of git)
The session in this folder is the manager for the acme programme.
- Other sessions never ask the owner directly. They send the manager the question,
  its options and a recommendation; the manager brings back the answer, which counts
  as the owner's own.
- A session pushes its own branch, opens its pull request, and merges it by rebase
  once every check is green. Someone else's pull request needs the owner's yes.
- Steps only the owner may take (secrets, money, permissions, public posts,
  deleting, force-pushes) go to the manager as one exact command.
- Only the manager restarts what runs live; workers use worktrees.
```
