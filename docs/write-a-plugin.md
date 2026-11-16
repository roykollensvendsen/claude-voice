# Write your own plugin

A plugin gives the voice new tools: a device at home, a service on the web, anything a
sentence can ask about. It is a small MCP server in a repository of its own. The bridge starts
it, lists its tools to the voice, and reads each answer aloud.

This guide is for any developer, on any machine. Nothing in it assumes this project's owner,
accounts or devices. The rules every plugin keeps are in [the plugin standard](plugin-standard.md);
this page says how to start one and why each part is there.

## Start from the template

`templates/plugin/` in this repository is a working plugin for a made-up device, the gizmo.
`claude-voice new-plugin <name>` will copy it under your plugin's name, into a folder of its own.
Until that command exists, copy the folder and replace `gizmo` with your name by hand.

The copy is green from its first commit. Its one tool, `<name>_status`, runs end to end through a
fake device, and every check passes with no network.

## What each part is for

| Part | Why it is there |
|---|---|
| `server.py` | The MCP tools, and nothing else. Each answer carries `say`, the sentence the voice reads aloud as it is. |
| `service.py` | The core. It asks the device and words every answer, so a failure is a sentence too, never a stack trace. |
| `device.py` | What the core needs from a device. The fake and the real adapter both answer it. |
| `fake.py` | A made-up device that refuses what the real one refuses. |
| `settings.py` | The plugin's own config, read from its own folder. A key it does not know is refused by name. |
| `cli.py` | `serve` for the bridge, and `status` to hear an answer in a terminal. |
| `platform/` | The one place that knows the operating system. |
| `claude-voice-plugin.toml` | How the bridge installs the plugin. It is data, checked against [the manifest format](plugin-manifest/v1.json). |
| `config.default.toml` | The first config install writes, with made-up values only. |
| `scripts/ratchet.py` | The quality levels. None may get worse than main's without a reason in the pull request. |
| `scripts/mutate.py` | Switches each rule off in turn, to prove its own test can fail. |
| `scripts/temproot.py` | Gives each run of the tests, the mutation check or the ratchet a temporary folder of its own. A test that leaves a file there fails the run. A stopped run cleans up, and a killed run's folder goes at the next start. |
| `justfile` | Every check as a plain command, the same lines CI runs. |

The layers run one way. The core never imports MCP, and only the server and the command line
put the core together. `scripts/ratchet.py` names the layers, and a test refuses an import
that crosses them.

## The fake refuses what the device refuses

A fake that says yes to everything lets a test pass that the real device would fail. So the
fake keeps the device's own limits: how often it may be asked, how long it may take, and which
values it accepts. The template's fake allows six reads a minute, and can be told to stop
answering.

When you learn a new limit of the real device, write it into the fake first, with a test. Then
write the code that copes with it.

Every stand-in that waits has its own limit: a fake that never answers, a step that waits for a
stop, a child process that sleeps. A test that starts a child stops it in a `finally` block.
Otherwise one hang holds uv's lock for hours under a mutation run. A process number can be
reused by another program. So the test kills a saved number only while `/proc/<pid>/cmdline`
still carries its own marker, such as its `tmp_path`, and then waits until the process is gone. Before it relies on that command line, it waits until it shows the child's own
arguments: right after the start it can still be empty, or show the parent's. Never add a marker
argument to a `sleep` stand-in, because GNU `sleep` rejects it as an invalid time interval and
exits at once. Run `[sys.executable, "-c", "import time; time.sleep(30)", marker]` instead, so
the test uses the interpreter it runs under.

## A test fails before the code

A test that passes on its first run proves nothing. Write the test, watch it fail, then write
the smallest code that turns it green.

On a pull request, the `red-first` job proves it. It runs your new and changed tests against
the code before your change, and each must fail there. A test that pins behaviour that already
exists is excused by a line in the pull request body: `Red-first: existing behaviour - <test>`.
The job sees only test files, so a change to shared setup such as `tests/conftest.py` needs a
word in the pull request.

## One row per rule

A rule is a line of code marked `# RULE: what it says`. Each rule needs three things:
- a test whose name is the rule's own words, such as `test_the_fake_refuses_a_read_sooner_than_the_device_allows`;
- a row in a file of its own under `scripts/mutations/`, named after the rule;
- that test going red when the row switches the line off.

`tests/test_rules_have_evidence.py` refuses a rule missing a row or a test. `scripts/mutate.py`
checks the third, and fails when another test went red instead. It also fails when the code
marks rules and no rows are there at all.

A mutation run is never given a time limit, in CI or anywhere else. A run cut short proves
nothing, so remove a limit rather than raise it.

## Commits

A commit message follows Conventional Commits, with its subject in the imperative. A session
adds one trailer only, `Co-Authored-By`. An outside contributor's `Signed-off-by` is never
refused. A session link, or any other note, belongs in the pull request's text. `just setup`
turns on a hook that refuses the rest while you write, and CI checks every commit again.

## What is GitHub's alone

The checks are plain commands, so any CI can run them. Three things use GitHub itself:
- the workflows in `.github/workflows/`, which another CI copies from the justfile;
- keeping main's measured levels as a run's artifact, which the ratchet reads on a pull request;
- branch protection and rebase-only merges, set on the repository.

Elsewhere, the ratchet measures the base itself, which takes longer. Measured levels never go
into the repository, on any host.

## The extras

`new-plugin` will only write files in a new folder. Everything else is offered as a command to
run yourself, and nothing runs unless you do:
- create the remote repository and push main;
- protect main, on GitHub;
- give a Claude Code session the rights to build it, from the bridge's `plugin-builder` group;
- add the plugin to an org chart, when you run one;
- install it in your own bridge, with `claude-voice plugins install`, then approve it by typing its name.
