# The rules for a voice plugin

These are the rules every plugin keeps, numbered so a finding can say "R28 fails at
tests/x.py:12". A number is never reused: a rule that goes keeps its number, marked withdrawn.
New rules come here by pull request, never into a local design.

They were moved here from a local design, new-plugin-v8.md (sha256
904799e0d91cb4f19a5b1237e4a49eb2e5e5a791cf62baf354cd8c4fd49111d4), with its history left out.
A rule here may drop history, never a must or a never. From this move on, this page is the
reference copy, and reviews cite its numbers.

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

**A plugin starts from `claude-voice new-plugin <name>` once that command exists; until then,
from a copy of `templates/plugin/` at main**. A missing file is copied from the template at
main, never from a neighbouring plugin.

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

**A commit's trailers may be `Co-Authored-By`, plus an outside contributor's `Signed-off-by`,
and nothing else**. A session link or any other note belongs in the pull request's text. The
commit-msg hook refuses one early, and continuous integration is the gate.

Template: `scripts/no_session_links.py` has `Co-Authored-By`

### R14

**Each test session has a private temporary root, and it is empty at the end**. Its parts:

- The root is set in a `tryfirst` `pytest_configure`, before pytest reads `--basetemp`.
- `TMPDIR` and `tempfile.tempdir` point into it, so every subprocess inherits it.
- `--basetemp` lies inside it, so a run never uses the shared `pytest-of-<user>` folder.
- At the end, anything left beside pytest's own basetemp fails the run, naming each path.
- An xdist worker uses the controller's root, with a `gw<n>` folder under it.
- `pytest_unconfigure` puts back `TMPDIR` and `tempfile.tempdir` as they were.

Never set `tmp_path_retention_policy = "none"` or `tmp_path_retention_count = 0`. Never remove
anything under `pytest-of-*`. A test refuses either setting in `pyproject.toml` and the conftest.
The conftest imports nothing that calls `gettempdir()` at module level. No module in `src/`
keeps a temporary path as a module constant.

Proof: a test checks that `tmp_path`, `tempfile.gettempdir()` and a subprocess's `$TMPDIR` all
lie under the run's root. Subprocess tests in a scratch project show three things. A leaked file
fails the run. A killed run's folder is swept at the next start. A subprocess leak is caught.

Template: `tests/conftest.py` has `pytest.ExitCode.TESTS_FAILED`
Template: `pyproject.toml` has `tmp_path_retention_policy = "failed"`

## How a run cleans up after itself

No test, mutation or ratchet run may leave a temporary file or folder behind, an interrupted
run included. R15 to R18 belong to the sweep; R19 to R22 to the runners.

R15 and R17 each have three equal designs. A repository uses one, names it in its conftest
docstring, and is reviewed against that one:

- **A, the starting name**: R15 (a) and R17 (a).
- **A2, the renamed lock file**: R15 (a2) and R17 (a). It is recommended for new code.
- **B, the parent guard**: R15 (b) and R17 (b).

### R15

**A run folder is locked before anything can sweep it**, by one of three designs:

- **(a) Lock first, then name.** A run makes `starting-<random>` with `mkdtemp`, then creates
  `.lock` in it with `O_CREAT|O_EXCL|O_NOFOLLOW`, mode 0600. It takes `flock(LOCK_EX)`, then
  claims `run-<n>` with `mkdir` and renames the starting folder onto it.
- **(a2) The renamed lock file.** The folder is made at its final random name with `mkdtemp`.
  `.lock-starting` is created in it with `O_CREAT|O_EXCL`, locked, then renamed to `.lock`.
- **(b) The parent guard.** The private 0700 parent (R18) is opened, and `flock(LOCK_EX)` is
  taken on that fd. It is held while a run makes its folder, locks its folder's own fd, and makes
  its final `rmdir`. The sweep holds the same parent lock while it looks.

In every design, the locked folder is never `--basetemp` itself, since pytest empties and remakes
that. `--basetemp` is a folder inside the locked run folder, or beside its `.lock`.

Template: `scripts/temproot.py` has `os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW`

### R16

**The sweep never creates a lock**. It opens an existing `.lock` only, with
`O_RDWR|O_NOFOLLOW` and no `O_CREAT`. It takes a folder only if `flock(LOCK_EX|LOCK_NB)`
succeeds. A folder whose lock is held is never touched.

Template: `scripts/temproot.py` has `os.O_RDWR | os.O_NOFOLLOW`

### R17

**A folder that cannot be locked is dead only when its design says so**:

- **(a), for designs A and A2: only after a grace period.** It is `STARTING_SECONDS`, an hour,
  marked in the code as a guess, for `run-` and `starting-` names alike. The age comes from the
  first `lstat`, which is reused. A folder another sweep removed in between is passed over, not
  a failure. Every entry must lstat as a directory owned by `getuid()`.
- **(b), for design B: at once.** Under the parent lock, a folder that can't be locked is being
  made by nobody. It needs no grace period, and no owner check of each entry.

In every design, removal follows no link (`lstat`, and `rmtree` refusing links), gives back
rights first, and takes only names of the run pattern.

Template: `scripts/temproot.py` has `STARTING_SECONDS = 60 * 60`

### R18

**The parent folder is ours**. A fixed-name folder in the shared temp area, such as
`<package>-mutate-<hash>`, is opened with `O_NOFOLLOW`. After opening, `fstat` checks it is a
directory, owned by `getuid()`, with `mode & 0o077 == 0`. This is checked at the start of a run
as well as in the sweep. Anything else stops the run, with a message naming the folder and the
command to fix it.

An older folder of our own may be one that others can only read or enter, such as 0755
(`mode & 0o022 == 0`). It may be tightened to 0700 by `fchmod` on the checked fd. A RULE goes on
that line. A folder
others may write (`mode & 0o022`) is refused, since it may already hold their entries.

**Not applicable** when a repository has no fixed-name parent folder, with every folder made by
`mkdtemp` or `TemporaryDirectory`. The pull request body then says "Point 4: not applicable, no
fixed-name folder". The reviewer checks it with a grep for `gettempdir()` joined to a constant
name.

### R19

**Every stop cleans up, and a second stop can't cut it short**. `mutate.py` and `ratchet.py`
handle SIGINT, SIGTERM and SIGHUP. The handler first sets all three to `SIG_IGN`. For TERM and
HUP it then raises `SystemExit(128 + n)`. For INT it raises either `KeyboardInterrupt` or
`SystemExit(130)`. Nothing in the code may catch it and go on.

Proof: one real subprocess test sends SIGINT, waits for a slowed cleanup to start, and sends
SIGTERM. It accepts exit code 130 or death by SIGINT (-2), and finds the source restored and the
folder gone. A test that only checks the handlers are installed isn't enough.

Template: `scripts/temproot.py` has `signal.SIG_IGN`

### R20

**One mutation run per checkout**. `mutate.py` takes a non-waiting exclusive lock
(`LOCK_EX|LOCK_NB`) on the checkout for the whole run. A second run stops at once with a sentence
saying one is already running. `mutate.py --repair-only` puts back any source a killed run left
mutated, and does nothing else.

Template: `scripts/mutate.py` has `--repair-only`
Template: `scripts/mutate.py` has `fcntl.LOCK_EX | fcntl.LOCK_NB`

### R21

**Tests leave the stop handlers as they found them**. An autouse fixture in `tests/conftest.py`
saves SIGINT, SIGTERM and SIGHUP before each test and restores them after it, with its own proof
test. Any test that runs `mutate.main()` or `ratchet.main()` in-process relies on it.

Template: `tests/test_stop_handlers.py` has `def test_`

### R22

**The ratchet passes a stop to its child alone, and waits**. Its parts:

- The child (`mutate.py`, or a measuring step) starts with `start_new_session=True`, with its
  RULE, so a terminal Ctrl-C reaches it only once, through the ratchet.
- Its output goes to temporary files, never pipes.
- On a stop, the ratchet sends SIGINT to the child alone, with `step.send_signal(signal.SIGINT)`,
  never `os.killpg(..., SIGINT)`. A `uv run` wrapper forwards SIGINT too, so a killpg reaches the
  inner Python twice.
- It then `wait()`s with no time limit.
- `killpg` is only for the final kill, which the docstring names as `kill -9 -- -<child pid>`.
- `ProcessLookupError` from the signal is ignored only when `step.poll()` shows the step has
  ended, and raised otherwise.

Proof: a Ctrl-C sent to the ratchet's group must reach the child exactly once, which kills the
`start_new_session=False` mutant. A real `uv run` chain, stopped once, gives the inner Python one
SIGINT, and its cleanup finishes.

Template: `scripts/ratchet.py` has `start_new_session=True`
Template, not built yet: `scripts/ratchet.py` has `step.send_signal(signal.SIGINT)`

### R23

**The package comes from this checkout**. Its parts:

- Each worktree has its own `.venv`, and `UV_PROJECT_ENVIRONMENT` is never pointed at a shared
  one.
- Before measuring, `mutate.py` and `ratchet.py` refuse to start when `<pkg>.__file__` isn't
  under this checkout's `src/`, site-packages included. The refusal names both paths and the fix,
  `uv sync` in this checkout.
- The check runs in a subprocess, with the interpreter and environment the measurement will use:
  `python -c "import <pkg>; print(<pkg>.__file__)"`.
- Neither script imports the package at module level.
- The ratchet checks each copy it measures, the base's included, by the tested tree's `ROOT`
  passed as an argument.

A suite that imports no Python package skips the check.

Template: `scripts/own_code.py` has `__file__`

### R24

**Every temporary folder is removed by a `with` or a `finally`**. It is a
`tempfile.TemporaryDirectory()` in a `with` block, or a `mkdtemp` removed in `finally`. A static
check, `tests/test_temp_files.py`, refuses a bare `mkdtemp`, an unremoved `mkstemp` and
`NamedTemporaryFile(delete=False)`.

Template: `tests/test_temp_files.py` has `def test_`

## How a run cannot hang

### R25

**The mutation run prints one flushed line per rule**: the rule's name, then killed or survived,
then the seconds it took. It is printed with `flush=True` or `python -u`, so pipe buffering can't
hide it. A stuck run then shows the rule it is stuck on.

Template, not built yet: `scripts/mutate.py` has `flush=True`

### R26

**Every wait inside a test is bounded by the test itself**. A subprocess wait, a queue get, an
event wait or a socket read each has its own short limit in the test, and running out fails that
test. This is not a time limit on the mutation run, which has none.

### R27

**Withdrawn as a request, and kept as a never**. A per-test or per-run timeout in the config,
such as pytest-timeout, is not asked for, and not allowed. A mutant that loops forever is found
by R25's line, and fixed by bounding a wait in the rule's own test (R26).

### R28

**A test starts every child that can fork through `run_bounded`**. The reference copy is
weather-mcp's `tests/bounded.py` and `tests/test_bounded.py` at
19b9da34436ae22e40d175a945d91e4e7d70ede0. Copy from that file, not from this text. Its parts:

- It starts the child with `start_new_session=True`.
- On `TimeoutExpired`, it sends `os.killpg(pgid, SIGKILL)`, then `communicate()` to collect the
  output, then fails the test with it.
- It never sends SIGINT or SIGTERM to the group.
- A bare `timeout=` is accepted only where the command can't fork, such as a plain `git`.

Proof: a shell starts a `sleep` grandchild of at least 30 s that prints its own pid. The test
checks that `os.kill(grandchild, 0)` raises `ProcessLookupError`, waiting briefly with a bound. It
also asserts the call returned within 15 s of its 1 s bound. Checking through the shell's pid, or
`killpg(pgid, 0)` alone, isn't allowed. The pull request body shows that the test goes red with
`child.kill()` in place of `killpg`.

Template: `tests/bounded.py` has `start_new_session=True`
Template: `tests/bounded.py` has `os.killpg(child.pid, signal.SIGKILL)`
Template: `tests/test_bounded.py` has `assert time.monotonic() - started < 15`

### R29

**A test's scratch run of the mutation script is bounded by the test, never by the script**.
`run_suite(timeout=None)` takes an optional bound. Its default, None, leaves a real run with no
limit, and only tests pass one: 300 s, one number for every repository.

Template, not built yet: `scripts/mutate.py` has `timeout=`

### R30

**A line from a running child is read with `line_within(stream, seconds)`**, copied only where a
test reads such a line. `readline()` runs on a daemon thread and `queue.get(timeout=seconds)`
waits, so `queue.Empty` fails the test. It is never `select()`, which sees the OS pipe and not a
buffer that may already hold the line.

Proof: a child that never writes fails within the bound, and two lines written at once are both
read.

Template: `tests/bounded.py` has `def line_within(`

### R31

**A process is ended by its own pid, after checking its `/proc/<pid>/cmdline` is still the one
you started; never by pattern**. That means never `pkill`, `killall` or `pkill -f`. It applies to
tests, cleanups and sessions by hand. In a test, the pid comes from the child itself (`$!`, a pid
file).

A grandchild that calls `setsid` itself leaves the group and escapes `killpg`. That is accepted,
since test code doesn't do it. If one ever does, it needs its own cleanup in that test.

## How a plugin is documented

### R32

**`docs/setup.md` is the agent setup guide, and the main document**. Its numbered steps are each
a fenced command block followed by a `Check:` line. The `Check:` line names the command whose
output proves the step worked. It links to each outside project's own docs rather than copying
them. An agent can follow it alone, top to bottom.

### R33

**setup.md has a fixed heading, `## Steps only the owner can do`**. Each step there is marked
`<!-- owner -->`. It says why only the owner can do it, and how the agent knows it's done. The
heading holds at least one step or the line "None.". Public text says "the owner", never a name.

### R34

**The README is for people, and its step list is generated from setup.md**. `scripts/docs.py`
writes it between `<!-- steps:begin -->` and `<!-- steps:end -->`. `tests/test_docs.py`, copied
from the template, fails in CI when:

- the README's block differs from what `scripts/docs.py` makes now, with the message to run
  `uv run scripts/docs.py`;
- a step has no `Check:`;
- the owner heading is missing, or holds no step and no "None.".

The test runs a command only when its block is marked `<!-- safe-to-run -->`. It then runs it
with `HOME`, `XDG_CONFIG_HOME`, `XDG_DATA_HOME` and `XDG_STATE_HOME` in `tmp_path`, under
`run_bounded`. Every other step command is parsed and listed, never run. `Check:` lines are
checked as present and non-empty, never run. The private-data scan covers `docs/`. `docs` is a
code-health row, like the complexity bar.

Template, not built yet: `tests/test_docs.py` has `safe-to-run`

## What the owner approved before the move

### R35

**A basic security review comes before the first approval**. It covers any web or HTTP surface,
such as a route that acts without a login or an origin check.

### R36

**A restart is safe**: it lets a running call finish.

### R37

**Upstream work is public**. A pull request's text on a public fork is read by strangers. A
security finding in someone else's code is held unpushed until the owner decides how to report
it.

### R38

**A limit that isn't documented is measured once on the real device**, with the date written
next to it. A tool counts as done only after one live try on the real device. The fake knows
only the limits someone wrote down, so this and R3 are the backstop.

## How each rule is proven

- **A rule in `src/` or `scripts/`** is a `# RULE:` line, with a mutation row and a red-first
  test.
- **A rule in `tests/conftest.py`**, where the mutation script doesn't look, is proven by a named
  red-first test. A sabotage test adds a subprocess run in a scratch project with the conftest's
  step turned off, which must then fail. The pull request body names both tests.
- **Or a row**, where the repository's mutation script can mutate `tests/conftest.py` and the
  test that must go red is in another file.
- **The mutation script does not scan `tests/`.** A mutated test judged by itself proves
  nothing, so the sabotage test stays the proof.
