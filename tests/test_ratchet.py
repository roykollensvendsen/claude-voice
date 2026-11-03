"""The quality ratchet: a change may make the code better or leave it as it is, never worse unless it says why."""

import json
import pathlib
import sys
import textwrap

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / "scripts"))

import ratchet

BASE = {
    "coverage": 85.0,
    "mutation": {"killed": 20, "total": 20},
    "tests": 400,
    "lint_errors": 0,
    "type_errors": 0,
    "cycles": 0,
    "complexity": {"pkg/a.py": 10, "pkg/b.py": 5},
    "test_seconds": 40.0,
    "test_budget": {"seconds": 100, "tolerance": 0.25},
}


def measured(**changes):
    return {**json.loads(json.dumps(BASE)), **changes}


def problems(base, now, committed=None, exceptions=()):
    return ratchet.compare(base, now, committed if committed is not None else now, set(exceptions))


# -- what gets worse ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("change", "metric"),
    [
        ({"coverage": 83.0}, "coverage"),
        ({"mutation": {"killed": 19, "total": 20}}, "mutation"),
        ({"tests": 399}, "tests"),
        ({"lint_errors": 1}, "lint_errors"),
        ({"type_errors": 2}, "type_errors"),
        ({"cycles": 1}, "cycles"),
        ({"complexity": {"pkg/a.py": 11, "pkg/b.py": 5}}, "complexity"),
    ],
)
def test_a_measure_that_gets_worse_fails_the_check(change, metric):
    found = problems(BASE, measured(**change))
    assert found and all(metric in p for p in found)


def test_coverage_may_wobble_a_little_between_machines():
    assert problems(BASE, measured(coverage=84.7)) == []


def test_a_new_module_starts_its_own_baseline():
    now = measured(complexity={"pkg/a.py": 10, "pkg/b.py": 5, "pkg/c.py": 12})
    assert problems(BASE, now) == []


def test_a_measure_may_get_worse_when_the_change_says_why():
    now = measured(coverage=80.0, tests=390)
    assert problems(BASE, now, exceptions={"coverage", "tests"}) == []
    assert problems(BASE, now, exceptions={"coverage"}) != []


def test_an_exception_is_written_in_the_pull_request_as_a_line_of_its_own():
    body = (
        "Some text.\n\nRatchet-Exception: coverage - the old module was deleted\nratchet-exception: tests: merged two\n"
    )
    assert ratchet.exceptions(body) == {"coverage", "tests"}
    assert ratchet.exceptions("We talked about a Ratchet-Exception: coverage once.") == set()


# -- the baseline moves with the code ----------------------------------------------------


def test_a_baseline_that_was_not_raised_fails_the_check():
    better = measured(coverage=88.0, tests=410)
    found = problems(BASE, better, committed=BASE)
    assert found and any("ratchet.py update" in p for p in found)
    assert problems(BASE, better, committed=better) == []


def test_the_suite_must_stay_inside_its_time_budget():
    assert problems(BASE, measured(test_seconds=124.0)) == []  # inside the tolerance
    found = problems(BASE, measured(test_seconds=126.0))
    assert found and "test_seconds" in found[0]


def test_updating_records_the_new_measures_and_keeps_the_budget(tmp_path):
    path = tmp_path / "quality.json"
    path.write_text(json.dumps(BASE))
    ratchet.write(path, {**measured(coverage=90.0), "test_budget": None})
    after = json.loads(path.read_text())
    assert after["coverage"] == 90.0 and after["test_budget"] == BASE["test_budget"]


# -- what is measured --------------------------------------------------------------------


def test_complexity_is_counted_per_function_and_a_nested_function_counts_on_its_own(tmp_path):
    source = textwrap.dedent(
        """
        def outer(x):
            if x:
                return 1
            def inner(y):
                for i in y:
                    if i and y:
                        return i
            return inner

        def flat():
            return 0
        """
    )
    (tmp_path / "m.py").write_text(source)
    assert ratchet.complexity(tmp_path) == {"m.py": 4}  # inner: 1 + for + if + and


def test_a_cycle_among_the_modules_is_counted_and_a_lazy_import_is_not(tmp_path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "a.py").write_text("from . import b\n")
    (pkg / "b.py").write_text("from .a import thing\n")
    (pkg / "c.py").write_text("def later():\n    from . import a\n")
    (pkg / "d.py").write_text("from . import c\n")
    assert ratchet.cycles(pkg) == 1


# -- layers, duplication and new modules -------------------------------------------------


def test_a_module_that_imports_from_a_higher_layer_is_a_breach(tmp_path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "low.py").write_text("from . import high\n")
    (pkg / "high.py").write_text("from . import low\n")
    (pkg / "loose.py").write_text("")
    found = ratchet.layer_breaches(pkg, {"low": 0, "high": 1})
    assert set(found) == {"low (layer 0) imports high (layer 1)", "loose has no layer"}


def test_the_bridge_keeps_to_its_layers():
    assert ratchet.layer_breaches(ratchet.PACKAGE, ratchet.LAYERS) == []


def test_duplicated_code_is_the_share_of_lines_in_blocks_that_appear_twice(tmp_path):
    block = "\n".join(f"x{i} = compute({i})" for i in range(6))
    (tmp_path / "a.py").write_text(block + "\n\nunique_a = 1\n" + "\n".join(f"y{i} = {i}" for i in range(3)) + "\n")
    (tmp_path / "b.py").write_text("    " + block.replace("\n", "\n    ") + "\nunique_b = 2\n")
    assert ratchet.duplication(tmp_path) == round(100 * 12 / 17, 2)  # 12 of 17 non-blank lines


@pytest.mark.parametrize(
    ("change", "metric"), [({"layer_violations": 1}, "layer_violations"), ({"duplication": 3.0}, "duplication")]
)
def test_layering_and_duplication_may_not_get_worse(change, metric):
    base = {**BASE, "layer_violations": 0, "duplication": 2.0}
    found = problems(base, {**base, **change})
    assert found and all(metric in p for p in found)


def test_a_base_without_a_new_measure_has_nothing_to_compare_with():
    now = {**BASE, "layer_violations": 0, "duplication": 2.0}
    assert problems(BASE, now) == []


def test_a_new_module_must_keep_every_function_at_fifteen_or_below():
    now = measured(complexity={"pkg/a.py": 10, "pkg/b.py": 5, "pkg/new.py": 16})
    found = problems(BASE, now)
    assert found and "pkg/new.py" in found[0]
    assert problems(BASE, measured(complexity={"pkg/a.py": 10, "pkg/b.py": 5, "pkg/new.py": 15})) == []
