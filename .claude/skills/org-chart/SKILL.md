---
name: org-chart
description: Find the Claude Code sessions that belong together and write or update the programme file (.claude/org.toml) that makes them one programme in the bridge's org chart - survey the running sessions and existing org files, propose a programme with components and roles that follow what has worked, write it only after the owner's yes, keep it out of git, and check it the way the bridge loads it. Use whenever the owner says "make an organisation", "make a programme", "group these sessions", "these sessions belong together", "org chart" or "org.toml", asks why a session shows under "Other", or wants a new plugin or session added to an existing programme as a component or a role.
---

# Making a programme

What a programme, a component and a role are, and every field of the file, is
in [docs/org-chart.md](../../../docs/org-chart.md); read it first. The helper
script needs the bridge's own Python. In a clone, run it with `uv run python`;
with the installed tool, use `$(uv tool dir)/claude-voice/bin/python`.

## 1. Survey

<!-- not run: reads the owner's running sessions, which differ on every machine -->
```
uv run python .claude/skills/org-chart/scripts/org_survey.py survey
```

It prints JSON with:

- every running session, with its folder, its main repository and its place;
- the programmes already defined, and the problems the bridge sees;
- each repository's git remotes, the role words its CLAUDE.md, CLAUDE.local.md
  or AGENTS.md use, and the other repositories those files mention;
- `candidate_groups`: repositories linked by a parent folder, a name prefix, a
  mention, or being the bridge's plugin, each link with its reason;
- `bridge_hints`, when the bridge offers `programme_hints`; prefer its groups.

A session with no `programme` is in none. The groups are guesses. Read the
notes files of each repository in a group before you trust it. A home folder
holding everything is no evidence, and the script ignores it.

## 2. Propose

For each group worth keeping, propose one programme. Read
[references/patterns.md](references/patterns.md) first, and propose the roles
that work, not only the ones that exist. Say which patterns the group lacks.
A programme with no architect gets one when its first proposal arrives.

- `code`: short, lowercase, from the main repository's name; not one in use.
- `components`: one per repository, with `folders` relative to the main
  repository (`"."`, `"../acme-vision"`) and its `remotes` as owner/name.
- `roles`: from session names and what the notes files say. Each gets
  `purpose`, `limits` and `send_here`.

Show a short table (programme, component or role, folder or session) and then
the full TOML. Ask the owner to confirm or edit it. Never write before a yes.

Never put a person's name, a child's name, an IP address or a device name in
the file or in any public text. When the only session for a role is named
after a person or a machine, propose renaming it first, with `rename_session`.

## 3. Write

1. Save the draft and check it the way the bridge reads it:

   <!-- not run: checks a draft in the owner's own repository -->
   ```
   uv run python .claude/skills/org-chart/scripts/org_survey.py check <main repo> --draft <draft.toml>
   ```

   An error (exit 1) must be fixed. A warning, such as a role whose session is
   not running, is about how things stand now; say it to the owner.
2. Write it to `<main repo>/.claude/org.toml`. A main folder that is not a
   git repository is best: nothing in it can be committed by mistake.
3. In a git repository, keep it out of git. This adds it to `.git/info/exclude`:

   <!-- not run: changes the owner's own repository -->
   ```
   uv run python .claude/skills/org-chart/scripts/org_survey.py exclude <main repo>
   ```
4. Run `check <main repo>` again and report its problems list as it is.
5. Offer the local manager rule in references/patterns.md as the main
   folder's `CLAUDE.local.md`, if it has none. Write it only on a yes.
6. Offer rules for what each role may and may not do, following "Rights" in
   references/patterns.md. Write them only on a yes.
7. Propose how the programme runs day to day, following "Running a programme
   day to day" in references/patterns.md. When running sessions join it, ask
   each for its handover, and read them before recommending anything.
8. Offer a night shift, following "Night shift" in references/patterns.md.
   Its queue and the shared morning list stay outside every repository. Start
   one only on the owner's yes.

The bridge reads only files in folders it knows: its clone, its plugins'
folders, and folders directly under the projects folder. When the main
repository is elsewhere, say so; the owner may move it or add it as a plugin.

## Rights

A role's `limits` line is advice; the bridge does not enforce it. Only rules
hold. Rights per org role, applied when the bridge starts a session, are
planned and not built. What exists today:

- `claude-voice permissions` writes rules to a repository's
  `.claude/settings.local.json`. They hold for every session in that folder.
- A start role in `roles.toml` with `guard = true` gets a push guard. It holds
  only when the bridge starts that session.

A message never changes a session's rights. Where each rule goes is in
references/patterns.md.

## When a session is blocked

Read the refusal text before proposing anything:

- **"denied by the Claude Code auto mode classifier"**: Claude Code's own
  safety check. Do not propose an allow rule. Give the owner the exact command.
- **"Permission to use … has been denied"**: a deny or ask rule in a settings
  file. Either the owner changes the rule, or the owner runs the command.
- **A hook's reason**, often naming the hook or the guard: either the hook is
  changed, or the owner runs the command.

Never work round a block by another route.

## Updating a programme

Read the existing file, change only what was asked, and go through steps 2
and 3 with the change shown as a diff:

- **A new plugin**: add `[components.<code>]` with its folder relative to the
  main repository, and its remote.
- **A new session**: add its name to a role's `sessions`, or a new
  `[roles.<code>]` with `component` set when the role belongs to one.

When a proposal is corrected by the owner for a reason this skill missed, say
which step should have caught it and propose the line for that step.
