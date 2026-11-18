# Reviewing a plugin

You need: [SKILL.md](SKILL.md) and [rules.md](rules.md). Nothing here assumes you built the
plugin.

Run the plugin's code only in a sandbox, as the `review-contribution` skill says. Write the
verdict in the format the review rules in claude-voice give; this page does not repeat it.

## How a finding is written

Each finding names the rule, the file and line, and what fails. For example: R28 fails at
`tests/test_cli.py:40`, where a shell child is started with a bare `timeout=`. A verdict written
before the move may cite v8's points; the table at the top of rules.md gives their rules.

## The check, point by point

| Point | Rule | How to see it | What fails it |
| --- | --- | --- | --- |
| 1 | R1 | Read each refusal and error text | One with no next step |
| 2 | R2 | Each tool's first description line | Over 78 characters, or not a sentence |
| 3 | R3 | Each tool that changes something | No read-back after the command |
| 4 | R4 | Each tool that cannot be undone | Acting without a token from the owner's yes |
| 5 | R5 | `scripts/private_data_scan.py --tracked --history` | Any finding |
| 6 | R6 | The fake beside the client | A limit the client knows that the fake does not refuse |
| 7 | R7 | `tests/test_cold_start.py` | Missing, or measuring the clock instead of CPU time |
| 8 | R8 | The ratchet's complexity measure | A new function over 15 |
| 9 | R9, R10 | Diff the repository against `templates/plugin/` | A file written by hand where the template has one |
| 10 | R11 | Every `# RULE:` line, and its row | A rule with no row, or no test named after it |
| 11 | R12 | The pull request's red-first job | A new test that passed on the old code, without an excuse line |
| 12 | R13 | `scripts/no_session_links.py` on the commits | Any trailer but the two allowed |
| 13 | R14 to R24 | The cleanup files: `tests/conftest.py`, `scripts/temproot.py`, the runners | Any of these rules not met, named by number |
| 14 | R25 to R31 | Every wait and every child a test starts | An unbounded wait, a bare `timeout=` on a child that can fork, a kill by pattern |
| 15 | R32 to R34 | `docs/setup.md` and the README | A step with no `Check:`, a stale README block, a run of an unmarked block |
| 16 | R5 | Every public text | A name, number, address or programme name of anyone |

## Two questions for every change

- Does anything make a temporary file or folder without removing it, a stopped run included?
- Does anything remove temporary files it did not make, such as a sweep of a shared folder?

## A run of a helper with a sabotage proof

A pull request that adds `run_bounded` shows in its body the sabotage result: with
`child.kill()` in place of the group kill, the test goes red (R28). Without it, it is not
approved.
