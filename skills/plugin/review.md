# Reviewing a plugin

You need: [SKILL.md](SKILL.md) and [rules.md](rules.md). Nothing here assumes you built the
plugin.

Run the plugin's code only in a sandbox, as the `review-contribution` skill says. Write the
verdict in the format the review rules in claude-voice give; this page does not repeat it.

## How a finding is written

Each finding names the rule, the file and line, and what fails. For example: R28 fails at
`tests/test_cli.py:40`, where a shell child is started with a bare `timeout=`. A verdict written
before the move may cite v8's points; the table at the top of rules.md gives their rules.

Check a rule against its whole text in rules.md, its "Proof:" line included. A rule's proof is
part of the rule: a test that misses it fails the rule. The last section of rules.md says how
each kind of rule is proven.

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
| 12 | R13 | `scripts/no_session_links.py` on the commits | Any trailer but `Co-Authored-By` and an outside contributor's `Signed-off-by` |
| 13 | R14 to R24 | The cleanup files: `tests/conftest.py`, `scripts/temproot.py`, the runners | Any of these rules or their proofs not met, named by number; for R15 and R17, against the design the conftest names |
| 14 | R25 to R31 | Every wait and every child a test starts | A test that waits without a bound it sets itself, named by file and line |
| 14a | R30 | Every line a test reads from a running child | A line read with `select()` instead of `line_within` |
| 14b | R31 | Every process a test, a cleanup or a session ends | One ended by pattern, or by pid without its cmdline checked |
| 15 | R25 | The mutation script's output | No flushed progress line per rule |
| 16 | R27, R28 | The test config, and every child a test starts | A per-test or per-run timeout in the config; a child that can fork started without `run_bounded` |
| 17 | R32 to R34 | `docs/setup.md` and the README | A step with no `Check:`, a stale README block, a run of an unmarked block |
| 18 | R5 | Every public text | A name, number, address or programme name of anyone |
| 19 | R35 | The plugin's web or HTTP surface, before its first approval | No security review, or a route that acts without a login or an origin check |
| 20 | R36 | A restart during a running call | The call is cut off instead of finishing |
| 21 | R37 | The plugin's pull requests to others' projects | Private detail in public text, or a security finding pushed before the owner decided |
| 22 | R38 | Each limit the fake copies, and each tool called done | A measured limit with no date, or no live try on the real device |
| 23 | R39 | The mutation script's handling of a row that does not compile | It counts as killed, the run passes, or the row is not named on the console and in the report |

## A pull request to the template itself

A template pull request that touches a file named in a "not built yet" line of rules.md is read
against that rule's whole text. The check in `tests/test_plugin_skill.py` sees only the exact
words in the line, so a part built in other words would leave a stale mark.

## Two questions for every change

- Does anything make a temporary file or folder without removing it, a stopped run included?
- Does anything remove temporary files it did not make, such as a sweep of a shared folder?

## A run of a helper with a sabotage proof

A pull request that adds `run_bounded` shows in its body the sabotage result: with
`child.kill()` in place of the group kill, the test goes red (R28). Without it, it is not
approved.
