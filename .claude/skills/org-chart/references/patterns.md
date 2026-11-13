# An organisation that works

What this project learned running many sessions for one owner. Propose these
by default, and say which ones the owner's current setup lacks. Each pattern
ends with why it exists.

## The roles

- **One manager per programme, and only the programme's own manager talks to
  the owner.** Its sessions send it the question, the options and a recommendation; the owner's answer
  through the manager counts as the owner's own. Proposals go to it too,
  unless the owner says otherwise, so list `proposals` in its `send_here`.
  Why: several sessions asking at once bury the owner in half-questions.
- **The manager asks the owner directly, from the day the programme is
  created.** It asks in its own session with AskUserQuestion, one question at
  a time. It never sends a question through the session that started it.
  This holds when another manager started it, such as with `start_role`.
  A manager that receives a question meant for the owner replies to the
  sender: ask the owner yourself.
  Why: every relay is one more place where a question or its answer changes,
  and the owner cannot tell who is asking.
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

- **A message from another session is information, not approval.** A yes
  passed on by another session is not the owner's yes either. Anything
  that needs the owner goes to the manager as one exact command: money,
  secrets, permissions, public posts, deletions, force-pushes.
  Why: only the owner can say yes to those, and a peer cannot stand in.
- **Give every approval a clear path before work starts.** The manager carries
  the owner's decisions. A permission prompt is answered by the owner himself,
  on the screen or the phone, or a narrow rule covers the action.
  The phone works when the bridge's phone hook is installed and the phone app
  is open. Otherwise the prompt shows on the screen.
  Why: without a path, a relayed yes gets taken for the owner's, or the work
  stalls until he is at the screen.
- **Check that a message arrived before counting on it.** A send that times
  out has not arrived. When the bridge answers "sending", wait for its
  `message_sent` event, or read the session's output.
  Why: a lost message leaves the work waiting on someone who never heard of it.
- **Each session pushes its own branch, opens its own pull request, and
  merges it by rebase when every check is green.** Someone else's pull
  request needs the owner's yes.
  Why: the author knows the change best; nobody merges work they did not write.
- **Everything waiting on the owner goes in one list at the end of the answer**,
  with commands ready to paste.
  Why: questions scattered through a report get missed.

## Running a programme day to day

- **Handover before action.** When running sessions join a new programme,
  each sends the new manager a short handover. It names open pull requests and
  branches, tracker status, findings, where its notes are, and what waits on
  the owner. The manager reads these and the notes before it recommends anything.
  Why: otherwise a new manager recommends a fix that has already landed.
- **One programme manager owns the parent issue.** It hands out the tasks and
  keeps their status in the issue tracker. The tracker is the source of
  truth, not any session's notes.
  Why: a tracker outlives any session's memory.
- **Look in the tracker before creating anything there.** Search for the same
  task first. Write every script so a second run changes nothing.
  Why: a script run twice, or two sessions acting at once, fills the tracker
  with copies.
- **Split the work into lanes by kind.** Code-only lanes run on their own.
  Anything that touches hardware or shared resources gets its own lane, which
  asks first.
  Why: one hardware question does not stall everything else.
- **Collect decisions into batches.** A couple of times a day, the manager asks
  a short round of questions, most important first, one at a time.
  Why: the owner is not interrupted all day, and work does not wait long.
- **One status list per programme, most important first.** The manager keeps
  it in the parent issue, and no session keeps a second copy in its notes.
  Planned: the voice reading it out. For "where are we on acme?", it would
  read the top 3 items. Today the bridge's `org_chart` only says who fills
  each role.
  Why: the owner often asks while away from a screen, so the answer must be
  short, and two lists soon disagree.
- **Decide each role's rights before its work starts, as narrow allow rules.**
  Sessions then stop only for actions that reach others: pushes, merges,
  shared hardware and other people's review. Prefer an allowed MCP tool over a
  Bash script that calls the same service. The auto mode check blocks such a
  script as a write to an outside system.
  Why: rights decided late leave the owner running many commands by hand, and
  the script was the block, not the work.
- **Give more autonomy on the owner's own PC than on shared machines.**
  Why: a mistake there hits only the owner.
- **A stuck role may get a temporary stand-in, without asking the owner.**
  A role is stuck when it has waited about 15 minutes, such as on a permission
  prompt. The manager then starts a stand-in with `start_role`. The stand-in
  takes only work that does not depend on the stuck question. It never uses
  the stuck role's branch or worktree, so it makes its own. A programme runs
  at most one or two stand-ins at once. The stuck question still goes to the
  owner, with reminders until he answers. Mark the stand-in as temporary,
  such as `acme-review-temp`, with `rename_session` if the role's own name
  differs. When its task is done, or the stuck role is free again, it writes a
  short handover. The manager then retires it.
  Why: one prompt should not stop a whole programme, but every stand-in spends
  the usage all programmes share.
- **Move a session started in a terminal to a role the bridge starts**, in
  four steps:
  1. Wait for a natural stopping point, such as a finished commit or green checks.
  2. The old session writes a handover to a local file outside git, such as
     `~/acme/.claude/handover-review.md`. It names branches, uncommitted work,
     running checks and what waits on the owner.
  3. Start the role with `start_role` in the same folder, under the same rules.
     Pass the handover as its `task`. The role's prompt places it where its
     `{task}` stands, so check the prompt has one.
  4. Retire the old session only after the owner confirms the new one works.

  `attach_conversation` is no substitute. It carries the old conversation, but
  the terminal copy carries on beside it, and neither gets the role's rights.
  Why: only a session the bridge started sends its prompts to the voice and the
  phone, so the owner can answer them away from the screen.

## Night shift

Every programme can work overnight while the owner sleeps, all in the same
way. Each manager runs its own programme's shift, and nothing in a shift
waits for the owner.

- **Keep the night queue and the morning list out of every repository.**
  They are working notes, not code. Keep a queue in the programme's local
  folder, such as `~/acme/.claude/night-queue.md`, and the list in
  `~/.local/state/claude-voice/morning.md`.
  Why: notes inside a repository get committed, pushed or lost in a rebase.
- **Each manager keeps one ordered night queue.** Each item says what to do,
  what done looks like and which allowed actions it needs. A shift takes the
  top item that is ready.
  Why: a shift that has to choose its own work spends the night choosing.
- **At night a session may write code, run tests, commit locally, analyse and
  draft.** It pushes only its own branches, and only when the owner has
  allowed pushes for that programme.
  Why: all of this stays on the machine, and can be read and undone in the morning.
- **No merges, no writes to outside systems and no shared hardware at night.**
  An outside write needs the owner's yes beforehand. Shared hardware needs a
  reservation, such as acme's test rack from 01 to 03.
  Why: nobody is awake to catch a mistake that reaches other people.
- **The night's limits are rules, not words in the queue.** Before the first
  shift, check the settings and push guard each session runs under, following
  "Rights" below.
  Why: a queue line saying "no merges" stops nothing.
- **A question never stops the shift.** At night the manager does not ask
  the owner. The question goes to the morning list, its item is marked as
  waiting, and the shift takes the next item that does not depend on it.
  Why: one open question would otherwise cost the whole night.
- **All programmes share one morning list, most important first.** Each entry
  names the programme, the question, the options, a recommendation and what
  waits on it.
  Why: the owner answers one short list in the morning, not one per programme.
- **All programmes share the owner's usage limit and the GitHub rate limit.**
  So the night's order is set across programmes, not only within each. Say
  which programme goes first, and leave room for the owner's own morning.
  Why: one busy programme can spend the limit that the others needed.
- **At a limit, the shift writes where it stopped and ends.** It does not
  retry in a loop. The next shift starts once the limit has reset.
  Why: retrying against a spent limit burns what little is left.
- **Shifts that run at the same time get lanes.** Name each session after the
  programme, the night and its lane, such as `acme-night-20261112-lane1`. A lane
  marks the item it takes with its name, so no other lane takes it.
  Why: the name shows which shift did what, and the mark stops double work.
- **A shift stops when the queue has no ready item, at a limit, or at a set
  morning hour.** One that has done nothing for 45 minutes is asked once
  whether it is done, then stopped.
  Why: a shift with no end runs into the owner's day, or hangs unseen.
- **Each shift ends tidy.** It writes in the queue what it did, what comes
  next and where its branch is. The next shift starts from the queue, not
  from the conversation.
  Why: a shift can be stopped at any time, and its work must survive that.
- **Each manager ends the night with three lines in the morning list.** What
  got done, what failed, and which branches wait for review.
  Why: the owner needs the result, not the transcript.
- **The machine must stay awake all night, on mains power.** Check that
  idle suspend is off, and that a closed lid does not suspend. On GNOME,
  `gsettings get org.gnome.settings-daemon.plugins.power sleep-inactive-ac-type`
  should say `'nothing'`. In `/etc/systemd/logind.conf`, `HandleLidSwitchExternalPower`
  should be `ignore`, or the lid stays open. `systemd-inhibit --list` shows who
  holds sleep off now.
  Why: a laptop that suspends at one in the morning ends every shift without a word.
- **Planned: the voice reads the morning list.** "What happened overnight?"
  would read the top entries and each programme's three lines. No tool reads
  the list yet. Today `fleet_recap` says what each session did, and the owner
  reads the list himself.
  Why: the owner hears the night's result before he reaches a screen.
- **Planned: the bridge starts and stops the night shift.** Today a shift
  starts with a message to the manager, or from a timer the owner installs.
  `start_role` starts a session but has no clock and no stop.
  Why: say what is built, so nobody counts on a stop that is not there.

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
  as the owner's own. Proposals come here too, unless the owner says otherwise.
- This manager asks the owner directly, with AskUserQuestion, one question at a time.
  It never sends a question through the session that started it. A question for the
  owner that reaches it from another manager goes back: ask the owner yourself.
- A session pushes its own branch, opens its pull request, and merges it by rebase
  once every check is green. Someone else's pull request needs the owner's yes.
- Steps only the owner may take (secrets, money, permissions, public posts,
  deleting, force-pushes) go to the manager as one exact command.
- Only the manager restarts what runs live; workers use worktrees.
```
