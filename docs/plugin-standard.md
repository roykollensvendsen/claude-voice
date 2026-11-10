# The standard every voice plugin keeps

A plugin is a small MCP server the bridge offers the voice: the garage door, the
TV and the robot vacuum today. Each was built on its own, and each got some
things right that the others missed. This page is what they agreed to share, so
that a new plugin starts at the level the best of them reached, and so that one
plugin's lesson becomes all of theirs.

It was put together on 2026-11-10 from the three plugins' own reviews of
themselves, and accepted by the owner the same day. The [`write-plugin`](../.claude/skills/write-plugin/SKILL.md) skill
says how to build one; this page says what it must do once built.

## A. How a plugin behaves towards the voice

These hold for every plugin, wherever it lives, a fork included. Whether they
are met is decided by the voice that uses the tools, so it is asked after every
new or changed tool, as [voice-feedback.md](voice-feedback.md) describes.

1. **Names the voice can tell apart.** A tool's name says what it does, and no
   two names sound alike. The pairs that must not be confused (`vacuum_start`
   and `vacuum_stop`) are listed and tested.
2. **A first line seen whole.** Each description opens with one whole sentence
   that the Claude app shows before it loads the tool, about 26 characters. The
   figure is kept in one place, in the bridge, rather than copied into each
   plugin.
3. **An answer within the voice's time.** The Claude app gives up on a call
   after about 15 seconds, and the voice app after about 30. Every tool's
   longest path stays inside the limit. Work that takes longer keeps running and
   is said to be on its way. A test adds up each tool's real waits.
4. **Something to say, and what to do next.** Every answer has a `say` text,
   read aloud as it is, and one result field that a program can read (`success`
   or `outcome`, chosen once per plugin and documented). When it fails, `say`
   tells the user what to do next, never a bare "failed".
5. **Say what actually happened.** After an action, read the device's state back
   and say that: "Started. The vacuum is cleaning", or "I sent the start; the
   vacuum says it is docked". Never "OK".
6. **Fail closed.** When the state is unknown or the device does not answer,
   refuse and say why. Never guess.
7. **Two steps for what is risky.** Which actions are risky is the owner's
   judgement, decided per plugin and written down: a door yes, a vacuum start
   no. A risky action is a request, whose question is read aloud, and then a
   confirm with a one-time token that expires. Read aloud, "Switch it off?" sounds
   like something already happening, so the question says how things are and then
   asks: "The garage TV is on. Do you want me to switch it off?" Nothing risky happens on the
   request alone. Nothing is retried on the plugin's own initiative. The token
   is spent only once the action has been sent, so a confirm that never arrived
   can be tried again with the same token while it is fresh. A plugin may be
   stricter and spend the token on arrival, so a wrong answer cancels the
   question, if a rule and its test in that plugin say so. A spoken phrase the
   owner sets in their own file may stand in for the yes, if a decision record
   in that plugin says so.
8. **Hands off.** A device the owner has marked to be left alone is left alone
   by every tool, and a test says so by name.
9. **Read-only is marked.** A tool that changes nothing carries `readOnlyHint`.
   The instructions the plugin gives the bridge state its safety rules in a few
   lines.
10. **No secret through the voice.** Secrets live in the plugin's own files,
    readable by the owner alone. They never pass through the bridge or the
    conversation, and are never logged. A scan refuses them in the repository's
    history.
11. **Tried without the real thing.** A simulate mode runs a pretend device, and
    every answer then says "Simulated:" first, so a change can be tried by voice
    without opening a door.
12. **Each rule has a test named after it**, and after any change to a name or a
    first line, someone asks the voice what it sees.

## B. How the repository is run

These hold for a repository the owner runs. A plugin in someone else's project
keeps that project's own ways, and its voice part lives on a branch of the
owner's fork, with checks of its own there.

13. **Checks that actually run.** On every pull request and every push to main:
    lint, format, types, tests on every Python version claimed, conventional
    commits, and the actions pinned to a commit. A check that never runs, as on
    a private repository whose Actions are blocked, is a wish.
14. **The local check runs everything continuous integration runs**, the quality
    ratchet included, so a slip shows the same day.
15. **Rules that must fail when switched off.** Tests come first and are seen
    red. Every rule is marked `# RULE:` in the source and has a row in the
    mutation table. The mutation run switches each off in turn, and its own
    test must go red. It runs in continuous integration.
16. **A quality ratchet: nothing gets worse than main.** Main keeps its own
    measured levels with each run of its checks, so no pull request records them
    ([ADR-CV-009](../decisions/ADR-CV-009-main-keeps-the-quality-levels.md)).
    `Ratchet-Exception: measure - why` lines allow a deliberate step back.
17. **Documents that cannot drift.** Every command a page shows runs in a test.
    A block that is not run says why. A generated page has a check that fails
    when it is stale.
18. **The files a stranger looks for:** a README they can follow, CONTRIBUTING,
    AGENTS.md, SECURITY, CHANGELOG, a licence, decision records, and a list of
    what is deferred with the trigger that would make it worth doing.
19. **Every change goes through a pull request**, even in a repository with a
    single owner. Then its checks have run before anything reaches main, and
    main can be locked against direct pushes.
20. **One change per topic, landed by rebase**, with contributors thanked for
    what was good.

## What to copy from whom

| Practice | Where it is done best |
| --- | --- |
| Every tool answers within the voice's time, from the real constants | tv-mcp, `tests/test_voice_time_limit.py` |
| The first-line rule as a test over every feature | tv-mcp, `tests/test_tool_openings.py` |
| A budget for how many tools and characters the voice gets by default | tv-mcp, `tests/test_context_budget.py` |
| A scan that refuses private addresses in the repository | tv-mcp, `scripts/private_data_scan.py` |
| A slow cloud still answers inside the budget, saying what it knows | dreame-mcp, `Vacuum._guarded`, `test_a_slow_cloud_still_answers_within_the_budget` |
| Reading the state back after an action | dreame-mcp, `Vacuum._after` |
| A simulator on the clock, marked in every answer | dreame-mcp, `simulator.py`, `test_a_simulated_answer_says_so` |
| Rules that must fail when switched off, in Python and C++ | garage-door, `scripts/mutate.py` and `scripts/mutations.toml` |
| Documented commands that run in a test | garage-door, `tests/test_documented_commands.py` |
| A one-time token that a wrong answer cancels | garage-door, `GarageService.confirm`, `test_a_token_works_once` |

When a plugin does something better than this page asks, it goes in the table,
and the others take it up.
