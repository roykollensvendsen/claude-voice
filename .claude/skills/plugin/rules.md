# The rules for a voice plugin

These are the rules every plugin keeps, numbered so a finding can say "R28 fails at
tests/x.py:12". A number is never reused: a rule that goes keeps its number, marked withdrawn.
New rules come here by pull request, never into a local design.

They were moved here from a local design, new-plugin-v8.md (sha256
904799e0d91cb4f19a5b1237e4a49eb2e5e5a791cf62baf354cd8c4fd49111d4), with its history left out.
From this move on, this page is the reference copy, and reviews cite its numbers.

A line `Template: <file> has <text>` names what `templates/plugin/` must hold for the rule.
`tests/test_plugin_skill.py` checks each one. A line marked "not built yet" names a part the
template still lacks. That check fails the day the template gains it, so the mark comes off.

## Old verdicts: v8's points and their rules

A verdict written before the move cites v8's points. This table finds the rule for each.

| v8 point | Rule |
| --- | --- |
| v8 cleanup 1 | R15 |
| v8 cleanup 2 | R16 |
| v8 cleanup 3 | R17 |
| v8 cleanup 4 | R18 |
| v8 cleanup 5 | R19 |
| v8 cleanup 6 | R20 |
| v8 cleanup 7 | R21 |
| v8 cleanup 8 | R22 |
| v8 cleanup 9 | R23 |
| v8 hang 1 | R25 |
| v8 hang 2 | R26 |
| v8 hang 3 | R27 |
| v8 hang 4 | R25 |
| v8 hang 5 | R28 |
| v8 hang 6 | R29 |
| v8 hang 7 | R30 |
| v8 docs 1 | R32 |
| v8 docs 2 | R33 |
| v8 docs 3 | R34 |

## How a plugin behaves

### R1

**Every error says what is wrong and what to do next**. A refusal names the cause and the
owner's next step, with the exact command when there is one. A bare "not answering" is a bug.

### R2

**A tool's first line says what it is for,** in one sentence of at most 78 characters.
Field descriptions name every input form accepted, from the first tool on
([the plugin standard](../../../docs/plugin-standard.md), A.2).

### R3

**Every change is read back**. After a command the plugin reads the device again, and says
"not confirmed" when it does not show the change (standard A.5).

### R4

**What cannot be undone asks first**. It acts only on a confirmation token after the owner's
own spoken yes. Night and presence rules are written at design time, for each device.

### R5

**Personal setup lives in local config from day one**. PINs, addresses, device names and
logins never appear in the repository, logs or docs. Examples use made-up values.

Template: `scripts/private_data_scan.py` has `def offences(`

### R6

**The fake refuses what the device refuses**. It copies the documented limits: rate limits,
timeouts, attempt caps, expiry and accepted value shapes. Each limit is one constant the client
and the fake share, with one test that breaks it.

Template: `src/gizmo_mcp/fake.py` has `class`

### R7

**A cold start is fast**. The server's own CPU time, read after the child is reaped, stays
under half the bridge's 15 s start limit. A loose 60 s clock catches a hang. Nothing is sent and
nothing sleeps at start.

Template: `tests/test_cold_start.py` has `resource.RUSAGE_CHILDREN`

### R8

**No function is more tangled than the bar**. A new module keeps every function's McCabe count
at 15 or below, and the code-quality skill's limits hold from day one.

## How a plugin is built

### R9

**A plugin starts from the template, never by hand**. It is made with
`claude-voice new-plugin <name>`. A missing file is copied from `templates/plugin/` at main,
never from a neighbouring plugin.

### R10

**The walking skeleton is green from the first commit**. `<name>_status` runs end to end through
the fake. Each tool has a test file of its own, and a test checks that every tool has one.

Template: `tests/test_every_tool_has_a_test.py` has `def test_`

### R11

**Every rule has a mutation row and a test named after it**. Rows live one per file, in a folder
that is the source's whole path. The runner reads them at call time and fails when there are
none. `repair()`, and every function it calls by name, holds no rule.

Template: `tests/test_mutate_runner.py` has `def _inside_repair(`

### R12

**A new test fails first**. The red-first job runs a pull request's new tests against the code
before it, and each must fail there.

Template: `scripts/red_first.py` has `def `

### R13

**A commit carries no trailer but Co-Authored-By and Signed-off-by**. A session link belongs
nowhere in a commit. The commit-msg hook refuses one early, and continuous integration is the
gate.

Template: `scripts/no_session_links.py` has `Co-Authored-By`

### R14

**Each test session has a private temporary root, and it is empty at the end**. The root is set
before any test runs, and pytest's `--basetemp` lies inside it. Nothing under pytest's shared
`pytest-of-<user>` folder is ever removed.

Template: `tests/conftest.py` has `pytest.ExitCode.TESTS_FAILED`
Template: `pyproject.toml` has `tmp_path_retention_policy = "failed"`

## How a run cleans up after itself

No test, mutation or ratchet run may leave a temporary file or folder behind, an interrupted
run included. R15 to R18 belong to the sweep; R19 to R22 to the runners.

### R15

**A run folder is locked before it is named**. The folder is made at a random name, its `.lock`
is created exclusively, and only then is it locked and used. The locked folder is never
`--basetemp` itself, since pytest empties that.

Template: `scripts/temproot.py` has `os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW`

### R16

**The sweep never creates a lock**. It opens an existing `.lock` only, and takes a folder only
when a non-waiting exclusive lock succeeds. A folder whose lock is held is never touched.

Template: `scripts/temproot.py` has `os.O_RDWR | os.O_NOFOLLOW`

### R17

**A folder with no lock counts as dead only after a grace period**. The age comes from the first
`lstat`, which is reused. Removal gives back rights first and follows no link.

Template: `scripts/temproot.py` has `STARTING_SECONDS = 60 * 60`

### R18

**The parent folder is ours**. A fixed-name folder in the shared temp area is opened without
following links, then checked as a directory owned by us that no one else may enter. Anything
else stops the run with the fix.

### R19

**Every stop cleans up, and a second stop cannot cut it short**. The handler for SIGINT, SIGTERM
and SIGHUP first ignores all three, then raises. A real test sends SIGINT, then SIGTERM during a
slowed cleanup.

Template: `scripts/temproot.py` has `signal.SIG_IGN`

### R20

**One mutation run per checkout**. A second run stops at once and says one is running.
`--repair-only` puts back what a killed run left mutated, and does nothing else.

Template: `scripts/mutate.py` has `--repair-only`
Template: `scripts/mutate.py` has `fcntl.LOCK_EX | fcntl.LOCK_NB`

### R21

**Tests leave the stop handlers as they found them**. An autouse fixture saves and restores
SIGINT, SIGTERM and SIGHUP around each test, with its own proof test.

Template: `tests/test_stop_handlers.py` has `def test_`

### R22

**The ratchet passes a stop to its child alone, and waits**. The child starts in a session of
its own, writes to files rather than pipes, and gets SIGINT by itself. The ratchet waits with no
time limit.

Template: `scripts/ratchet.py` has `start_new_session=True`
Template, not built yet: `scripts/ratchet.py` has `step.send_signal(signal.SIGINT)`

### R23

**The package comes from this checkout**. Each worktree has its own `.venv`. Before measuring,
the scripts ask a subprocess where the package is imported from, and refuse when it is another
checkout.

Template: `scripts/own_code.py` has `__file__`

### R24

**Every temporary folder is removed by a `with` or a `finally`**. A static check refuses a bare
`mkdtemp`, an unremoved `mkstemp` and `NamedTemporaryFile(delete=False)`.

Template: `tests/test_temp_files.py` has `def test_`

## How a run cannot hang

### R25

**The mutation run prints one flushed line per rule:** its name, killed or survived, and the
seconds it took. A stuck run then shows the rule it is stuck on.

Template, not built yet: `scripts/mutate.py` has `flush=True`

### R26

**Every wait inside a test is bounded by the test itself**. A subprocess wait, a queue get, an
event wait or a socket read each has a short limit, and running out fails that test.

### R27

**Withdrawn**. It asked for a per-test pytest-timeout, against the rule that runs are never
capped. A mutant that loops forever is found by R25's line and fixed in its test by R26.

### R28

**A test starts every child that can fork through `run_bounded`**. On time, the whole group gets
SIGKILL, so no grandchild outlives the test. Its test asserts the call returned within 15 s.

Template: `tests/bounded.py` has `start_new_session=True`
Template: `tests/bounded.py` has `os.killpg(child.pid, signal.SIGKILL)`
Template: `tests/test_bounded.py` has `assert time.monotonic() - started < 15`

### R29

**A test's scratch mutation run is bounded by the test, never by the script**. `run_suite` takes
an optional bound of 300 s that only tests pass; a real run keeps none.

Template, not built yet: `scripts/mutate.py` has `timeout=`

### R30

**A line from a running child is read with `line_within`,** and only where a test needs it. It
reads on a daemon thread, so a child that never writes fails the test within the bound.

Template: `tests/bounded.py` has `def line_within(`

### R31

**A process is ended by its own pid, never by pattern**. Its `/proc/<pid>/cmdline` is checked
first, so a reused pid is left alone. `pkill` and `killall` are never used.

## How a plugin is documented

### R32

**`docs/setup.md` is the setup guide an agent follows alone**. Each numbered step is a command
block followed by a `Check:` line whose output proves it worked.

### R33

**Setup has a fixed heading, `## Steps only the owner can do`**. Each step there says why only
the owner can do it, and how the agent knows it is done.

### R34

**The README's step list is made from setup.md**. `tests/test_docs.py` fails when it is stale,
and runs only the blocks marked safe to run, with a private home, under `run_bounded`.

Template, not built yet: `tests/test_docs.py` has `safe-to-run`
