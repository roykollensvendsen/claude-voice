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


BUDGET = {"seconds": 100, "tolerance": 0.25}


def problems(base, now, exceptions=()):
    return ratchet.compare(base, now, set(exceptions), BUDGET)


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


def test_a_pull_request_need_not_record_the_levels_it_reached():
    better = measured(coverage=88.0, tests=410, complexity={"pkg/a.py": 8, "pkg/b.py": 5})
    assert problems(BASE, better) == []


def test_the_suite_must_stay_inside_its_time_budget():
    assert problems(BASE, measured(test_seconds=124.0)) == []  # inside the tolerance
    found = problems(BASE, measured(test_seconds=126.0))
    assert found and "test_seconds" in found[0]


def test_the_time_budget_is_the_one_thing_quality_json_keeps(tmp_path):
    path = tmp_path / "quality.json"
    path.write_text(json.dumps({"test_budget": {"seconds": 90, "tolerance": 0.1}}))
    assert ratchet.budget(path) == {"seconds": 90, "tolerance": 0.1}
    assert ratchet.budget(tmp_path / "missing.json") == ratchet.DEFAULT_BUDGET


def test_measured_levels_are_written_for_main_to_keep(tmp_path):
    out = tmp_path / "levels.json"
    ratchet.write_levels(out, {**measured(), "test_budget": None})
    assert json.loads(out.read_text()) == {k: v for k, v in BASE.items() if k != "test_budget"}


def test_mains_kept_levels_are_the_base_when_they_are_there(tmp_path):
    kept = tmp_path / "levels.json"
    kept.write_text(json.dumps(BASE))
    asked = []
    assert ratchet.base_levels(kept, lambda: asked.append(1) or {}) == BASE
    assert asked == []


def test_when_mains_levels_are_missing_the_base_is_measured(tmp_path):
    for kept in (None, tmp_path / "missing.json", tmp_path / "broken.json"):
        (tmp_path / "broken.json").write_text("{not json")
        assert ratchet.base_levels(kept, lambda: BASE) == BASE


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


STOPPED_RATCHET = """
import sys, tempfile, time
sys.path.insert(0, sys.argv[1])
import ratchet
ratchet.stop_cleanly()
with tempfile.TemporaryDirectory(dir=sys.argv[2]):
    print("ready", flush=True)
    time.sleep(60)
"""


@pytest.mark.skipif(sys.platform == "win32", reason="Windows has no polite stop signal to send")
def test_a_stopped_ratchet_still_removes_its_copy_of_main(tmp_path):
    """The ratchet measures main in a copy of it, and a stop signal used to leave that copy.

    Python runs no `finally` and no `with` exit on a stop signal unless it is told to, which is
    how whole copies of the repository piled up in the temporary folder.
    """
    import signal
    import subprocess

    scratch = tmp_path / "scratch"
    scratch.mkdir()
    script = tmp_path / "stopped.py"
    script.write_text(STOPPED_RATCHET)
    where = str(pathlib.Path(ratchet.__file__).parent)
    run = subprocess.Popen([sys.executable, str(script), where, str(scratch)], stdout=subprocess.PIPE, text=True)
    try:
        assert run.stdout.readline().strip() == "ready"
        run.send_signal(signal.SIGTERM)
        assert run.wait(60) == 128 + signal.SIGTERM
    finally:
        run.kill()
    assert list(scratch.iterdir()) == []


def test_the_ratchet_turns_a_stop_into_an_exit_before_it_starts(monkeypatch):
    seen = []
    monkeypatch.setattr(ratchet, "stop_cleanly", lambda: seen.append("stop"))
    monkeypatch.setattr(sys, "argv", ["ratchet.py", "check"])
    assert ratchet.main() == 1  # no --base: it stops before measuring anything
    assert seen == ["stop"]


@pytest.mark.skipif(sys.platform == "win32", reason="Windows has no hang-up signal")
def test_a_second_stop_cannot_cut_the_cleanup_short():
    """GitHub sends an interrupt and then a stop; the second must not end the cleanup halfway."""
    import signal

    kept = {s: signal.getsignal(s) for s in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT)}
    try:
        ratchet.stop_cleanly()
        with pytest.raises(SystemExit):
            signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
        assert {s: signal.getsignal(s) for s in kept} == dict.fromkeys(kept, signal.SIG_IGN)
    finally:
        for s, handler in kept.items():
            signal.signal(s, handler)


def test_a_stop_while_main_is_being_copied_still_removes_the_copy(monkeypatch, tmp_path):
    """The copy is made inside the ratchet's own folder, which goes whatever stops the run."""
    monkeypatch.setattr(ratchet.tempfile, "tempdir", str(tmp_path))

    def run(*args, **kwargs):
        if args[:2] == ("git", "fetch"):
            raise SystemExit(143)  # a stop the moment the copy has begun
        return ratchet.subprocess.CompletedProcess(args, 0, "abc123\n", "")

    monkeypatch.setattr(ratchet, "_run", run)
    with pytest.raises(SystemExit):
        ratchet.measure_at("main")
    assert list(tmp_path.iterdir()) == []


def _git(where, *args):
    import subprocess

    return subprocess.run(["git", *args], cwd=where, capture_output=True, text=True, check=True).stdout.strip()


def test_the_base_is_measured_where_no_other_branch_can_be_seen(monkeypatch, tmp_path):
    """A worktree shares every branch of the clone, and git keeps a record of it that a kill leaves."""
    here = tmp_path / "clone"
    here.mkdir()
    _git(here, "init", "-q", "-b", "main")
    _git(here, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q", "--allow-empty", "-m", "base")
    _git(here, "branch", "under-review")
    monkeypatch.setattr(ratchet, "ROOT", here)
    copy = tmp_path / "copy"
    ratchet.copy_of("main", copy)
    assert _git(copy, "rev-parse", "HEAD") == _git(here, "rev-parse", "main")
    assert _git(copy, "for-each-ref") == ""
    assert _git(here, "worktree", "list").count("\n") == 0  # no record of the copy in the clone


@pytest.mark.skipif(sys.platform == "win32", reason="Windows has no flock; there the sweep does nothing")
def test_a_killed_ratchet_s_copy_is_removed_at_the_next_start(tmp_path):
    import os

    left = ratchet.HeldFolder(dir=tmp_path)
    os.close(left._lock)
    left._lock = None
    ratchet.sweep(tmp_path)
    assert not left.path.exists()


@pytest.mark.skipif(sys.platform == "win32", reason="Windows has no flock; there the sweep does nothing")
def test_a_ratchet_copy_still_in_use_is_never_removed(tmp_path):
    import os
    import time

    held = ratchet.HeldFolder(dir=tmp_path)
    young, old = tmp_path / f"{ratchet.RUN_PREFIX}young", tmp_path / f"{ratchet.RUN_PREFIX}old"
    young.mkdir()
    old.mkdir()
    an_hour_ago = time.time() - 2 * 60 * 60
    os.utime(old, (an_hour_ago, an_hour_ago))
    try:
        ratchet.sweep(tmp_path)
        assert (held.path.exists(), young.exists(), old.exists()) == (True, True, False)
    finally:
        held.remove()
    assert not held.path.exists()


def test_the_ratchet_sweeps_before_it_measures(monkeypatch):
    seen = []
    monkeypatch.setattr(ratchet, "stop_cleanly", lambda: seen.append("stop"))
    monkeypatch.setattr(ratchet, "sweep", lambda: seen.append("sweep"))
    monkeypatch.setattr(sys, "argv", ["ratchet.py", "check"])
    assert ratchet.main() == 1
    assert seen == ["stop", "sweep"]
