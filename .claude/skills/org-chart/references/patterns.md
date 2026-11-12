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

## Running a programme day to day

- **One programme manager owns the parent issue.** It hands out the tasks and
  keeps their status in the issue tracker. The tracker is the source of
  truth, not any session's notes.
  Why: a tracker outlives any session's memory.
- **Split the work into lanes by kind.** Code-only lanes run on their own.
  Anything that touches hardware or shared resources gets its own lane, which
  asks first.
  Why: one hardware question does not stall everything else.
- **Collect decisions into batches.** A couple of times a day, the manager asks
  a short round of questions, most important first, one at a time.
  Why: the owner is not interrupted all day, and work does not wait long.
- **Planned: one status list per programme that the voice can read out.** For
  "where are we on acme?", it would read the top 3 items. The bridge cannot do
  this yet; it can only say who handles what.
  Why: the owner often asks while away from a screen, so the answer must be short.
- **Write narrow allow rules, so sessions stop only for actions that reach
  others.** Those are pushes, merges, shared hardware and other people's
  review. Prefer an allowed MCP tool over a Bash script that calls the same
  service. The auto mode check blocks such a script as a write to an outside
  system.
  Why: the script was the block, not the work.
- **Give more autonomy on the owner's own PC than on shared machines.**
  Why: a mistake there hits only the owner.

## Signs a programme needs a role

- Messages pile up between two sessions and neither has a manager.
  Why: without one, nobody decides and the thread just grows.
- The owner gets the same question from several sessions.
  Why: that is the work of a manager being done by everyone at once.

## Rights

What a role may do is set by rules, not by its `limits` line.

- **Planned: a local policy per programme, giving each role its rights.**
  The bridge would apply it when it starts the role's session. It does not
  exist yet. Today a start role in `roles.toml` can set `guard = true`. The
  bridge then starts it with a hook that refuses pushes to the upstream.
  Why: say what is built, so nobody counts on a check that is not there.
- **Rights given at start hold only for sessions the bridge started.** A
  session started in a terminal has none of them. To bring it under them,
  stop it and start it again through the bridge, such as with `start_role`.
  Why: a rule handed over at start never reaches a session started elsewhere.
- **Rules for one repository go in its `.claude/settings.local.json`**, which
  git ignores. `claude-voice permissions` writes them there. Never put them in
  the committed `.claude/settings.json`.
  Why: they are the owner's choices, not the project's, and others clone it.
- **A short list of hard denials goes in the user-wide `~/.claude/settings.json`**,
  such as `Bash(git push --force*)` or `Bash(git push * main)`. Keep it to what
  is never right on this machine.
  Why: a deny wins over every allow, so a long list blocks other work here too.
- **A message to a running session never changes its rights.** Rights change
  when the owner changes a settings file or answers a prompt, or when the
  session restarts with new settings.
  Why: if a message could widen rights, any session could hand itself more.

## When a session is blocked

Read the refusal text first. Each kind has its own way forward.

- **"Permission for this action was denied by the Claude Code auto mode
  classifier"**, often with a category, such as "Remote Shell Writes".
  Claude Code's own safety check stopped it. It catches writes to outside
  systems, merges without review and force-pushes. Do not answer it with a new
  allow rule. Give the owner the exact command to run himself.
  Why: auto mode drops broad allow rules, and widening rights is the owner's call.
- **"Permission to use Bash with command … has been denied"**: a deny rule in a
  settings file. An ask rule shows a question instead. Find the rule. Either
  the owner changes it, or the owner runs the command.
  Why: the rule was written on purpose; changing it is the owner's decision.
- **A refusal that names a hook**, such as the bridge's push guard, with its
  reason. Either the hook is changed, or the owner runs the command.
  Why: a hook is code someone put there for a reason, so read the reason first.

In every case, never get round the block by another route, such as another
tool or a copy of the file.
Why: a way round turns one refusal into a gap nobody knows about.

## Keep the programme's files private

`org.toml`, `CLAUDE.local.md` and `settings.local.json` are never committed
where others can see them.

- **Best: a main folder that is not a git repository**, such as a plain
  `acme` folder under the projects folder, with the repositories beside it.
  Why: nothing in it can be pushed, however a command is typed.
- **Otherwise list each file in `.git/info/exclude`**, which is never
  committed. The skill's `exclude` command does it for `org.toml`.
  Why: a line in `.gitignore` is itself committed, and tells others the file exists.

## The manager rule, kept local

Offer this for the programme's main folder as `CLAUDE.local.md`, which Claude
Code reads. Check that git does not commit it: list it in `.git/info/exclude`,
or keep it in a folder that is not a git repository. Replace `acme` with the
programme's code:

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
