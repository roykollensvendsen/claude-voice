# Bringing a plugin into the team

You need: [SKILL.md](SKILL.md) and [rules.md](rules.md). The manager's own role is in
[docs/organisation.md](../../../docs/organisation.md).

The manager does these in order, so a new plugin starts at the level the others reached. Each
step has a check; the next starts only once it holds.

## 1. The repository

- The owner says yes to the plugin and to its licence. On that yes, the manager runs
  `gh repo create`, public or private as the owner chose.
- The files come from the template ([builder.md](builder.md), step 1).
- After the first push, branch protection is applied as its own yes:
  `python ~/.claude/skills/start-repo/scripts/forge.py <owner>/<name>`.

Check: a pull request is the only way onto its main, and a force-push is refused.

## 2. The session and its roles

- A builder session of its own, started in the plugin's folder and told the rules in
  docs/organisation.md. It asks the owner nothing directly, and reports to the manager.
- Reviewers: the quality role and the architect.
- The plugin joins the org chart through the `org-chart` skill.

Check: its first status message arrives within five minutes. A silent session may be stuck on a
prompt nobody sees, such as folder trust.

## 3. Registration and approval

1. `[plugins.<name>]` goes in `~/.config/claude-voice/config.toml`, with `branch` if its checkout
   follows another branch than main.
2. The owner types `claude-voice plugins approve <name>` in a plain terminal, and types the name
   when asked. Hand it over as one command, never as a `!` line, and never type it for them.
3. A plugin new to the settings needs one bridge restart. After that, the bridge picks up each
   approval by itself.

Check: `health` shows the plugin `running` after its first call.

After this, merged code with the same tools runs after `plugin_restart`. A tool that is added,
removed or changed is approved by voice in developer mode, once merged. Only a new command or
folder is typed again.

## 4. The health pages

The plugin is a component in the programme's org file, and has a row on the code-health page.

Check: both pages show it.

## 5. Cleanup and recovery

List the plugin's folders as kept or cleanable, and what moving to another machine takes or
leaves.

## 6. Secrets

A new secret path joins the secrets table in claude-voice before its first use, in its own pull
request. Secrets are collected with the `secret-box` skill, never pasted.

## 7. Checked by voice

After the restart, call one tool through the bridge and see what the voice sees. Then ask the
voice what it needs, as [docs/voice-feedback.md](../../../docs/voice-feedback.md) says.

## The gates the manager holds

- What the owner types, and when: the approval, a secret, a login, folder trust.
- Never approve a plugin for the owner.
- Never put a tool on the allow-list that the plugin's own session has not called safe for voice.
