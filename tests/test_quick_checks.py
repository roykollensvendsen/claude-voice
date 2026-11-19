"""Two quick checks to run before every push, so a session needs no throwaway script.

`mutate.py --only FILE` turns off only the rules guarding that one source file, in seconds
instead of the whole table's tens of minutes. `ratchet.py check --base REF --fast` compares
what can be read from the code alone (the most tangled function per module, the layers, the
import circles, the copied code) with the base, and checks the rule rows, with no suite and
no mutation run. Continuous integration still runs everything.
"""

import importlib.util
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).parent.parent


def _script(name: str):
    spec = importlib.util.spec_from_file_location(f"{name}_quick", ROOT / f"scripts/{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _mutate():
    return _script("mutate")


ratchet = _script("ratchet")


ROWS = [
    {"name": "a", "file": "src/claude_voice/a.py"},
    {"name": "b", "file": "src/claude_voice/b.py"},
    {"name": "c", "file": "src/claude_voice/a.py"},
]


# -- mutate.py --only ----------------------------------------------------------------------


def test_only_the_rules_of_the_named_file_are_turned_off():
    assert [r["name"] for r in _mutate().chosen(ROWS, ["src/claude_voice/a.py"])] == ["a", "c"]


def test_with_no_file_named_every_rule_is_turned_off():
    assert _mutate().chosen(ROWS, None) == ROWS


def test_a_file_named_by_its_row_folder_or_with_backslashes_is_the_same_file():
    mutate = _mutate()
    for named in ("scripts/mutations/src/claude_voice/a.py", "src\\claude_voice\\a.py", "./src/claude_voice/a.py"):
        assert [r["name"] for r in mutate.chosen(ROWS, [named])] == ["a", "c"], named


def test_a_file_with_no_rules_is_said_not_passed_over():
    with pytest.raises(SystemExit, match="src/claude_voice/none.py"):
        _mutate().chosen(ROWS, ["src/claude_voice/none.py"])


@pytest.mark.parametrize("kept", [["--json", "m.json"], ["--write", "t.md"]])
def test_a_run_of_some_rules_never_writes_the_figures(kept, monkeypatch):
    mutate = _mutate()
    monkeypatch.setattr(mutate, "stop_cleanly", lambda: pytest.fail("the run started"))
    with pytest.raises(SystemExit):
        mutate.main(["--only", "src/claude_voice/a.py", *kept])


# -- ratchet.py --fast ---------------------------------------------------------------------

TANGLED = "def f(x):\n" + "".join(f"    if x == {i}:\n        return {i}\n" for i in range(20))


def source(tmp_path: pathlib.Path, body: str = "def f():\n    return 1\n") -> pathlib.Path:
    package = tmp_path / "src" / "claude_voice"
    package.mkdir(parents=True)
    (package / "a.py").write_text(body)
    return tmp_path / "src"


def test_the_quick_measures_are_read_from_the_code_alone(tmp_path, monkeypatch):
    monkeypatch.setattr(ratchet, "_run", lambda *a, **k: pytest.fail(f"ran {a}"))
    found = ratchet.static_measures(source(tmp_path))
    assert found["complexity"] == {"claude_voice/a.py": 1}
    assert (found["cycles"], found["duplication"]) == (0, 0.0)
    assert "coverage" not in found and "mutation" not in found


def test_a_quick_check_fails_a_function_more_tangled_than_the_base(tmp_path, monkeypatch):
    base = ratchet.static_measures(source(tmp_path / "base"))
    monkeypatch.setattr(ratchet, "static_at", lambda ref: base)
    monkeypatch.setattr(ratchet, "SOURCE", source(tmp_path / "now", TANGLED))
    monkeypatch.setattr(ratchet, "rule_rows", list)
    found = ratchet.fast("main")
    assert found and all("complexity of claude_voice/a.py got worse" in p for p in found)


def test_a_quick_check_fails_rule_rows_that_do_not_match_the_code(tmp_path, monkeypatch):
    same = ratchet.static_measures(source(tmp_path))
    monkeypatch.setattr(ratchet, "static_at", lambda ref: same)
    monkeypatch.setattr(ratchet, "SOURCE", tmp_path / "src")
    monkeypatch.setattr(ratchet, "rule_rows", lambda: ["the rule rows do not match the code"])
    assert ratchet.fast("main") == ["the rule rows do not match the code"]


def test_the_rule_rows_are_checked_by_their_own_tests_alone(monkeypatch):
    ran = []

    class Done:
        returncode, stdout = 1, "a row is out of place"

    monkeypatch.setattr(ratchet, "_run", lambda *a, **k: ran.append(a) or Done())
    (problem,) = ratchet.rule_rows()
    assert "a row is out of place" in problem
    ((*_, target),) = ran
    assert target == "tests/test_rules_have_evidence.py"
    assert not any("mutate.py" in word for word in ran[0])


def test_the_base_for_a_quick_check_is_read_in_a_copy_that_is_removed(monkeypatch, tmp_path):
    monkeypatch.setattr(ratchet.tempfile, "tempdir", str(tmp_path))
    copies = []

    def copy_of(ref, copy):
        copies.append(copy)
        source(copy)

    monkeypatch.setattr(ratchet, "copy_of", copy_of)
    monkeypatch.setattr(ratchet, "_run", lambda *a, **k: pytest.fail(f"ran {a}"))
    assert ratchet.static_at("main")["complexity"] == {"claude_voice/a.py": 1}
    assert copies and not copies[0].exists()


def test_a_quick_check_never_runs_the_suite_or_the_mutations(monkeypatch, tmp_path):
    seen = []
    monkeypatch.setattr(ratchet, "stop_cleanly", lambda: None)
    monkeypatch.setattr(ratchet, "sweep", lambda: None)
    monkeypatch.setattr(ratchet, "fast", lambda ref: seen.append(ref) or [])
    monkeypatch.setattr(ratchet, "measure", lambda *a, **k: pytest.fail("measured in full"))
    monkeypatch.setattr(sys, "argv", ["ratchet.py", "check", "--base", "origin/main", "--fast"])
    assert ratchet.main() == 0
    assert seen == ["origin/main"]
