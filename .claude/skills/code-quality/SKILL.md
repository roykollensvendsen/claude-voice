---
name: code-quality
description: Make this repository's measures on the code-health page green, and keep them there - coverage, rules proven by a test, the most tangled function, layers, copied code, the time the tests take and the checks on main. Use when the page shows a measure in amber or red, before a pull request that adds or splits code, when someone asks how good the code is or how to raise a number, or when a measure seems impossible to meet and an exemption is tempting.
---

# Keeping the code-health page green

The bridge's software page (`http://127.0.0.1:8811/ui/`, read from
`GET /api/v1/health/software`) shows one row per repository and one cell per
measure. The numbers come from the levels main's checks keep after every merge,
so a pull request turns a cell green only once it has landed. This skill is the
routine for one repository: read the page, measure the same thing locally, fix
the worst cell test-first, land it.

## 1. What the page measures, and when it is green

| Cell | Green | Amber | Red |
| --- | --- | --- | --- |
| Test coverage | 80 % or more | 60 - 80 % | under 60 % |
| Rules proven by a test (mutations killed) | every one | 90 % or more | fewer |
| Most tangled function (McCabe) | 15 or less | up to 25 | above 25 |
| Modules in their layers | 0 imports out of order | | any |
| Copied code | under 3 % of lines | up to 6 % | more |
| Lint and type errors | 0 | | any |
| Import cycles | 0 | any | |
| Time the tests take | inside the budget | inside budget plus tolerance | over |
| Checks on main | passing | running | failed |

The limits are in `src/claude_voice/software.py`, `MEASURES`; read them there if
this table and the page ever disagree, and fix the table.

A cell can also read **exempt** (section 4). Exempt is never green, and nothing
in this skill makes a cell exempt by itself.

## 2. Measure it locally

Run these from the repository root, after `uv sync --all-extras`: in a fresh
clone or worktree the measure otherwise stops at "The suite fails". The long
ones go in the background; never put a time limit on them, because a cut-off
mutation run reports rules as unproven that are not. Test time measured while
other heavy runs share the machine comes out far too high; trust the figure
from continuous integration.

<!-- not run: the full measure takes about ten minutes -->
```
uv run python scripts/ratchet.py measure --out /tmp/levels.json   # every cell at once, as main's checks measure it
uv run python scripts/ratchet.py check --base origin/main         # what your branch made worse than main
uv run python scripts/mutate.py                                   # each rule switched off in turn
```

For one cell at a time:

<!-- not run: examples of measuring one cell -->
```
uv run pytest -q --cov --cov-report=term-missing        # coverage, with the lines no test runs
uv run pytest -q --durations=15                         # the slowest tests
uv run ruff check . && uv run mypy src                  # lint and type errors
```

The complexity, layer, cycle and duplication counts are functions in
`scripts/ratchet.py` (`complexity`, `layer_breaches`, `cycles`, `duplication`);
the `measure` output lists the worst function's score per module, so the module
to open first is the one with the highest number. The time budget is the one
thing in `quality.json`, and it changes only by hand, with the owner's yes.

## 3. Fix the worst cell, test-first

Work on one cell per pull request, the reddest first. Everything here follows
CONTRIBUTING.md: a failing test before the code, one logical change per pull
request, commits through the `write-commit` skill.

**Checks on main red.** Fix that before anything else: every other number is
measured by those checks, so while they fail the page stops moving.

**Coverage under 80.** Open the lines `term-missing` lists and ask what the
person using the code would notice if each were wrong. Write a test of that
behaviour, through the module's public functions, and see it red: for code that
already exists, break the line by hand (or with `mutate.py`), watch the test
fail, then put the line back. A test that passed the first time it ran proves
nothing. Never test a private helper just to lift the number, and never mark
code as excluded from coverage to get past the limit; that is an exemption, and
section 4 applies.

**A rule nobody proves.** `mutate.py` names each rule whose removal no test
noticed. A rule is a line marked `# RULE: what it says`; it needs a row of its
own in `scripts/mutations/<the source file's path>/<the rule's words>.toml` and
a test named in the rule's own words
(`# RULE: a spoken reply is capped` wants `test_a_spoken_reply_is_capped`).
Write that test, run the mutation, see that very test go red, and keep the row.
Never loosen a row or point it at something easier to make the count go up; if
a row has to change, the pull request says why.

**A function above 15.** Split it without changing what it does:

1. Run the module's tests and see them green. If nothing covers the function's
   branches yet, write those tests first, as a pull request of their own.
2. Pull one decision out at a time: a guard clause that returns early, a
   lookup table in place of a chain of `if`s, one helper per branch of a
   `match`, a loop body as a function. Each helper does one thing and is named
   for it.
3. Run the same tests and see them green again, unchanged. A split that needs
   a test edited has changed behaviour, and is not a split.
4. Commit it as `refactor:`, with no behaviour change in the same commit.

A nested function counts on its own, not towards the function around it, so
hiding branches in a closure is not a fix the page will reward; give the helper
a name at module level.

**An import out of its layer.** The layers are `LAYERS` in `scripts/ratchet.py`,
from foundations up to `cli`; a module may use, when it loads, only modules in
layers below its own. Move the shared code down into a lower module that both
can use, or pass the thing in as an argument. Do not hide the import inside a
function to dodge the count. A new module gets its layer in `LAYERS` in the
same pull request.

**Copied code at 3 % or more.** A block counts when six or more lines in a row
appear more than once, ignoring indentation. Put the block in one function both
places call, with the tests green before and after, as `refactor:`. Copied
tests count too; a shared fixture or helper in the test folder is the fix.

**Tests over their budget.** Find the slowest with `--durations=15`. The usual
cause is a real wait: a sleep, a timeout, a subprocess. Make the wait
injectable and pass a fake clock or a short timeout in the test. Raising the
budget is the owner's decision, not a fix.

**Lint, type errors or a cycle.** Fix them as they are reported; a cycle is
broken the same way as a layer breach, by moving shared code down.

**When you review code**, as a quality round does, add this to the checklist:
for each outside call, name the documented limit and the test that breaks it;
a missing one is a finding.

Before you push, run the gates in CONTRIBUTING.md and the ratchet against main.
The ratchet refuses anything worse than main; a pull request that must make a
measure worse says why on a `Ratchet-Exception:` line, and that is the owner's
call too.

## 4. Exemptions

Sometimes a cell should stay outside its limit: a function that is one long
match on a tool's name, a generated table, a test that has to wait for real
hardware. Such a cell may be marked exempt, and only like this:

- **A written reason**, saying why meeting the limit would make the code worse,
  not that it would take long.
- **The owner's yes.** Ask through the manager session with the exact entry
  and your recommendation; never add one on your own judgement, and never
  count silence as a yes.
- **In the repository's own `quality.toml`**, in a pull request of its own,
  one entry per measure, with the measure's id as the page names it:

<!-- not run: an example entry -->
```toml
[[exempt]]
measure = "complexity"
reason = "the tool table is one long match on the tool's name"
approved = "Roy, 2026-11-11, in pull request #212"
```

The page then shows that cell as exempt, with the reason, never as green. An
entry without both a reason and an approval is ignored, and a measure back
inside its limit is green again whatever the file says, so delete the entry
then. The ids are `coverage`, `mutation`, `tests`, `problems`, `cycles`,
`layers`, `duplication`, `complexity` and `test_time`.
Why it works this way is
[ADR-CV-013](../../../decisions/ADR-CV-013-an-agreed-exception-is-shown-as-exempt.md).

## 5. Land it

Push your own branch, open the pull request, and once every check is green
merge it with `gh pr merge --rebase`. Then wait for main's checks to finish:
only their kept levels move the page, and the page itself refreshes every ten
minutes. Look at the cell again; if it is still not green, start again at
section 3 with what it now says.
