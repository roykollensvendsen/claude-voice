"""The mutation runner's own rules, each with a test named after it.

`scripts/mutate.py` proves every other rule, so its own rules are proven the same
way: `scripts/mutations/scripts/mutate.py/` switches each off in turn, and the test
here that names it goes red. The runner has already loaded its code when it switches
a line off, and the suite it starts loads the switched-off file, so it can prove itself.
The way is garage-door's.

None of these runs a real suite: the suite is a stand-in that records what it was
asked to do, so a test here never mutates anything.
"""

import importlib.util
import pathlib
import subprocess

import pytest

ROOT = pathlib.Path(__file__).parent.parent


@pytest.fixture
def mutate():
    """The runner, loaded by path: scripts/ is not a package."""
    spec = importlib.util.spec_from_file_location("mutate_runner", ROOT / "scripts/mutate.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _suite_says(monkeypatch, mutate, stdout, returncode=1):
    """Stand in for the suite: it answers `stdout`, and the arguments it got are kept."""
    asked = {}

    def run(args, **kwargs):
        asked.update(args=args, **kwargs)
        return subprocess.CompletedProcess(args, returncode, stdout, "")

    monkeypatch.setattr(mutate.subprocess, "run", run)
    return asked


def test_a_file_changed_during_the_run_is_never_restored_over(tmp_path, mutate):
    """Someone saved the file while the suite ran: putting the original back would lose their edit."""
    source = tmp_path / "service.py"
    source.write_text("edited while the suite ran\n")
    assert mutate.restore(source, "original\n", "mutated\n") is False
    assert source.read_text() == "edited while the suite ran\n"


def test_two_mutations_never_share_a_bytecode_cache(monkeypatch, mutate):
    """Two equal-size mutations in one second would otherwise import the last one's bytecode."""
    # Unset here, so the runner has to set it itself: a mutation run sets it for the suite it starts.
    monkeypatch.delenv("PYTHONDONTWRITEBYTECODE", raising=False)
    asked = _suite_says(monkeypatch, mutate, "1 passed\n", returncode=0)
    mutate.run_suite()
    assert asked["env"]["PYTHONDONTWRITEBYTECODE"] == "1"


def test_a_coloured_failure_line_is_still_attributed(monkeypatch, mutate):
    """With colour on, FAILED is wrapped in codes, and a kill must still name its test."""
    coloured = (
        "\x1b[31mFAILED\x1b[0m tests/test_fake.py::test_the_fake_refuses_a_read_sooner_than_the_device_allows"
        " - assert\n"
        "\x1b[31m1 failed\x1b[0m\n"
    )
    _suite_says(monkeypatch, mutate, coloured)
    assert mutate.run_suite() == (1, ["test_the_fake_refuses_a_read_sooner_than_the_device_allows"])


def _repository(tmp_path, *, marked):
    (tmp_path / "src/pkg").mkdir(parents=True)
    (tmp_path / "scripts/mutations").mkdir(parents=True)
    (tmp_path / "src/pkg/core.py").write_text("# RULE: a rule\n" if marked else "x = 1\n")
    return tmp_path


def test_rules_marked_with_no_rows_fail_the_run(tmp_path, monkeypatch, mutate, capsys):
    """A rows folder emptied by mistake would leave every rule unproven and the run green."""
    monkeypatch.setattr(mutate.sys, "argv", ["mutate.py"])
    root = _repository(tmp_path, marked=True)
    monkeypatch.setattr(mutate, "ROOT", root)
    monkeypatch.setattr(mutate, "ROWS", root / "scripts/mutations")
    monkeypatch.setattr(mutate, "RECOVERY", root / "scripts/.mutate-recovery.json")
    # Its own temporary folder too: the real one holds a running mutation run's suite.
    monkeypatch.setattr(mutate, "MUTATE_TMP", tmp_path / "mutate-tmp")
    assert mutate.main() == 1
    said = capsys.readouterr().out
    assert "src/pkg/core.py mark rules" in said
    # The fix it names must be one a session may run: `git checkout -- ...` is refused to sessions.
    assert "git restore --source=origin/main scripts/mutations" in said
    assert "git checkout" not in said


def test_a_repository_with_no_rules_marked_passes_with_no_rows(tmp_path, monkeypatch, mutate):
    """A repository just scaffolded has nothing to prove yet."""
    monkeypatch.setattr(mutate.sys, "argv", ["mutate.py"])
    root = _repository(tmp_path, marked=False)
    monkeypatch.setattr(mutate, "ROOT", root)
    monkeypatch.setattr(mutate, "ROWS", root / "scripts/mutations")
    monkeypatch.setattr(mutate, "RECOVERY", root / "scripts/.mutate-recovery.json")
    # Its own temporary folder too: the real one holds a running mutation run's suite.
    monkeypatch.setattr(mutate, "MUTATE_TMP", tmp_path / "mutate-tmp")
    assert mutate.main() == 0


#: The one function whose rules cannot be proven: a killed run repairs with the code it left
#: switched off. The way is garage-door's.
UNPROVABLE = "repair"

REPAIR_WITH_A_RULE = '''
def repair():
    """Put back a file a killed run left mutated."""
    # RULE: a repaired file is the original
    if kept:
        write(kept)


def sweep():
    # RULE: a swept folder is gone
    remove()
'''


def _inside_repair(text, rows, mutate):
    """The rules marked, or switched off by a row, in `repair()` or any function it calls by name."""
    import ast

    defs = (ast.FunctionDef, ast.AsyncFunctionDef)
    functions = {f.name: f for f in ast.parse(text).body if isinstance(f, defs)}
    assert UNPROVABLE in functions, f"no {UNPROVABLE}() in the runner; if it was renamed, rename UNPROVABLE too"
    reached, waiting = set(), [UNPROVABLE]
    while waiting:  # repair() and everything it calls, however deep, within this file
        name = waiting.pop()
        reached.add(name)
        calls = [c for c in ast.walk(functions[name]) if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)]
        called = {c.func.id for c in calls}
        waiting += [c for c in called if c in functions and c not in reached]
    lines = text.splitlines()
    inside = {i for name in reached for i in range(functions[name].lineno - 1, functions[name].end_lineno)}
    marked = [lines[i].split("# RULE:")[1].strip() for i in sorted(inside) if "# RULE:" in lines[i]]
    rowed = [r["name"] for r in rows if mutate.line_of(text, r["find"], int(r.get("occurrence", 1))) in inside]
    return sorted(set(marked + rowed))


def test_a_rule_inside_repair_is_found(mutate):
    """The guard below is only worth something if it sees a rule where one has been put."""
    rows = [
        {"name": "a swept folder is gone", "find": r"^\s*remove\(\)$"},
        {"name": "kept", "find": r"if kept:"},
    ]
    assert _inside_repair(REPAIR_WITH_A_RULE, rows, mutate) == ["a repaired file is the original", "kept"]


def test_no_rule_lives_inside_repair(mutate):
    """A killed run repairs with the code it left switched off, so a rule in repair() cannot be proven."""
    text = (ROOT / "scripts/mutate.py").read_text(encoding="utf-8")
    rows = [r for r in mutate.all_rows() if r["file"] == "scripts/mutate.py"]
    assert rows, "the runner's own rows are missing"
    assert _inside_repair(text, rows, mutate) == []


REPAIR_CALLING_A_RULE = """
def write(kept):
    # RULE: a repaired file is written whole
    path.write_text(kept)


def repair():
    write(kept)
"""


def test_a_rule_in_what_repair_calls_is_found(mutate):
    """A helper repair() calls runs switched off too, so its rule cannot be proven either."""
    assert _inside_repair(REPAIR_CALLING_A_RULE, [], mutate) == ["a repaired file is written whole"]


def test_a_runner_with_no_repair_is_refused_by_name(mutate):
    """Renamed, repair() would leave the guard checking nothing; it says what it looked for."""
    with pytest.raises(AssertionError, match=r"no repair\(\) in"):
        _inside_repair("def mend():\n    pass\n", [], mutate)


REPAIR_AWAITING_A_RULE = """
async def write(kept):
    # RULE: an awaited repair is written whole
    path.write_text(kept)


def repair():
    run(write(kept))
"""


def test_a_rule_in_an_async_helper_repair_calls_is_found(mutate):
    """An `async def` helper runs switched off too, once repair() calls it."""
    assert _inside_repair(REPAIR_AWAITING_A_RULE, [], mutate) == ["an awaited repair is written whole"]


def test_the_mutation_report_counts_only_rules_whose_own_test_went_red(mutate):
    """A kill by another test is not evidence about the rule, so the figures never count it."""
    results = [
        ("proven", "src/a.py", 2, "test_proven", True),
        ("by luck", "src/a.py", 1, "test_by_luck", False),
        ("survived", "src/a.py", 0, "test_survived", False),
    ]
    figures = mutate.figures(results)
    assert (figures["killed"], figures["total"]) == (1, 3)
    assert [rule["own_test_red"] for rule in figures["rules"]] == [True, False, False]


def test_a_row_that_breaks_the_code_is_named_and_runs_nothing(mutate):
    assert mutate.broken("scripts/x.py", "def f(:\n") is not None
    assert mutate.broken("scripts/x.py", "x = 1\n") is None
    assert mutate.broken("README.md", "def f(:\n") is None
