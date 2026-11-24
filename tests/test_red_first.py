"""The red-first check: a pull request's new and changed tests must fail on the code before it.

The check reads which tests a pull request adds or changes, runs them against its
merge base with only the tests applied, and again on the branch. These tests feed
it small sources and fake runs, so none of them builds a tree or syncs a project.
"""

import pathlib
import subprocess
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / "scripts"))

import red_first


@pytest.fixture(autouse=True)
def _checkouts_in_tmp_path(tmp_path, monkeypatch):
    """Every run's checkout folder goes in the test's own folder, never the machine's.

    A mutation run switches off the folder's removal, and a run made without this then left an
    empty red-first-* folder in the machine's temporary folder each time.
    """
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))


OLD = """
def test_kept():
    assert add(1, 1) == 2


def test_gone():
    assert True
"""


def never_run(ids):
    raise AssertionError(f"nothing should run when there is nothing to prove, but {ids} were run")


def runs(before, after):
    """A fake run: every id gets the outcome given, before the change and on the branch."""

    def run(ids):
        return {i: before for i in ids}, {i: after for i in ids}

    return run


def test_a_pull_request_with_no_new_or_changed_tests_passes_red_first():
    ok, said = red_first.prove([], never_run, body="")
    assert ok
    assert "nothing to prove" in "\n".join(said)


def test_a_new_or_changed_test_must_fail_before_the_change():
    ok, said = red_first.prove(["tests/test_x.py::test_a"], runs("passed", "passed"), body="")
    text = "\n".join(said)
    assert not ok
    assert "passed before this change" in text
    assert "tests/test_x.py::test_a" in text
    assert "`Red-first: existing behaviour - test_a`" in text


def test_a_test_red_before_and_green_on_the_branch_is_proved():
    ok, said = red_first.prove(["tests/test_x.py::test_a"], runs("failed", "passed"), body="")
    assert ok, said


def test_the_red_first_marker_never_excuses_a_test_that_fails_on_the_branch():
    body = "Red-first: existing behaviour - all, pinning tests for a refactor\n"
    ok, said = red_first.prove(["tests/test_x.py::test_b"], runs("passed", "failed"), body=body)
    assert not ok
    assert "tests/test_x.py::test_b fails on this branch" in "\n".join(said)


def test_a_test_red_before_and_skipped_on_the_branch_is_not_proved():
    """It never ran on the branch, so it shows nothing; the marker is the way through."""
    ids = ["tests/test_x.py::test_windows_only"]
    ok, said = red_first.prove(ids, runs("failed", "skipped"), body="")
    assert not ok
    assert "tests/test_x.py::test_windows_only" in "\n".join(said)
    ok, _ = red_first.prove(ids, runs("failed", "skipped"), body="Red-first: existing behaviour - test_windows_only")
    assert ok


def test_a_test_red_before_and_on_the_branch_is_broken_not_proved():
    ok, said = red_first.prove(["tests/test_x.py::test_b"], runs("failed", "failed"), body="")
    assert not ok
    assert "fails on this branch" in "\n".join(said)


def test_a_named_marker_excuses_that_test_alone():
    ids = ["tests/test_x.py::test_a", "tests/test_x.py::TestC::test_b"]
    body = "Some text.\nRed-first: existing behaviour - test_a\n"
    ok, said = red_first.prove(ids, runs("passed", "passed"), body=body)
    text = "\n".join(said)
    assert not ok
    assert "excused" in text and "tests/test_x.py::test_a" in text
    assert "tests/test_x.py::TestC::test_b" in text.split("passed before", 1)[1]


def test_all_in_the_marker_excuses_every_test_that_passed_before():
    ids = ["tests/test_x.py::test_a", "tests/test_y.py::TestC::test_b"]
    body = "Red-first: existing behaviour - all, pinning tests for a refactor"
    ok, said = red_first.prove(ids, runs("passed", "passed"), body=body)
    assert ok, said
    assert "tests/test_y.py::TestC::test_b" in "\n".join(said)


def test_a_marker_must_stand_on_a_line_of_its_own():
    body = "We could write Red-first: existing behaviour - test_a here, but not inside a sentence."
    ok, _ = red_first.prove(["tests/test_x.py::test_a"], runs("passed", "passed"), body=body)
    assert not ok


def test_a_skipped_test_is_not_proved_until_the_marker_excuses_it():
    ids = ["tests/test_x.py::test_windows_only"]
    ok, said = red_first.prove(ids, runs("skipped", "skipped"), body="")
    assert not ok
    assert "skipped" in "\n".join(said)
    ok, _ = red_first.prove(ids, runs("skipped", "skipped"), body="Red-first: existing behaviour - test_windows_only")
    assert ok


def test_a_new_test_counts_and_an_unchanged_one_does_not():
    new = OLD + "\n\ndef test_new():\n    assert add(2, 2) == 4\n"
    counted, removed = red_first.count({"tests/test_x.py": (OLD, new)}, red_first.shapes_of([OLD]))
    assert counted == ["tests/test_x.py::test_new"]
    assert removed == []


def test_a_changed_test_counts_but_formatting_and_comments_do_not():
    reformatted = OLD.replace("assert add(1, 1) == 2", "# the sum\n    assert add(1,  1)  ==  2")
    counted, _ = red_first.count({"tests/test_x.py": (OLD, reformatted)}, red_first.shapes_of([OLD]))
    assert counted == []
    changed = OLD.replace("assert add(1, 1) == 2", "assert add(1, 1) == 3")
    counted, _ = red_first.count({"tests/test_x.py": (OLD, changed)}, red_first.shapes_of([OLD]))
    assert counted == ["tests/test_x.py::test_kept"]


def test_a_reworded_docstring_does_not_count_as_a_changed_test():
    with_doc = OLD.replace("def test_kept():\n", 'def test_kept():\n    """Two and two."""\n')
    counted, _ = red_first.count({"tests/test_x.py": (OLD, with_doc)}, red_first.shapes_of([OLD]))
    assert counted == []


def test_a_test_whose_body_only_moved_or_was_renamed_is_not_counted():
    renamed = OLD.replace("def test_kept", "def test_one_and_one_make_two")
    counted, _ = red_first.count({"tests/test_x.py": (OLD, renamed)}, red_first.shapes_of([OLD]))
    assert counted == []
    moved = "def test_kept():\n    assert add(1, 1) == 2\n"
    changes = {"tests/test_x.py": (OLD, OLD.split("def test_kept")[0]), "tests/test_y.py": (None, moved)}
    counted, _ = red_first.count(changes, red_first.shapes_of([OLD]))
    assert counted == []


def test_a_removed_test_is_listed_but_never_counted():
    without = OLD.split("def test_gone")[0]
    counted, removed = red_first.count({"tests/test_x.py": (OLD, without)}, red_first.shapes_of([OLD]))
    assert counted == []
    assert removed == ["tests/test_x.py::test_gone"]


def test_a_test_in_a_class_is_named_with_its_class():
    source = "class TestSum:\n    def test_a(self):\n        assert 1\n\n    def helper(self):\n        pass\n"
    counted, _ = red_first.count({"tests/test_x.py": (None, source)}, set())
    assert counted == ["tests/test_x.py::TestSum::test_a"]


def test_a_test_file_that_does_not_parse_on_the_branch_is_named():
    with pytest.raises(SystemExit, match="tests/test_x.py"):
        red_first.count({"tests/test_x.py": (None, "def test_a(:\n")}, set())


def git(folder, *args):
    return subprocess.run(["git", "-C", str(folder), *args], check=True, capture_output=True, text=True).stdout


def test_the_before_tree_has_the_branch_s_tests_and_the_base_s_code(tmp_path):
    repo = tmp_path / "repo"
    (repo / "tests").mkdir(parents=True)
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.email", "a@b.c")
    git(repo, "config", "user.name", "A")
    (repo / "code.py").write_text("OLD = 1\n")
    (repo / "tests/test_code.py").write_text("def test_old():\n    pass\n")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "base")
    base = git(repo, "rev-parse", "HEAD").strip()
    (repo / "code.py").write_text("NEW = 2\n")
    (repo / "tests/test_code.py").write_text("def test_new():\n    pass\n")
    (repo / "tests/data.txt").write_text("helper data\n")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "head")
    head = git(repo, "rev-parse", "HEAD").strip()

    assert red_first.changed_test_files(base, head, cwd=repo) == ["tests/test_code.py"]
    before = tmp_path / "before"
    red_first.before_tree(base, head, before, cwd=repo)
    try:
        assert (before / "code.py").read_text() == "OLD = 1\n"
        assert "test_new" in (before / "tests/test_code.py").read_text()
        assert (before / "tests/data.txt").read_text() == "helper data\n"
    finally:
        git(repo, "worktree", "remove", "--force", str(before))


def test_the_outcomes_are_read_per_test_including_a_module_that_cannot_import(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/test_gone.py").write_text("from nowhere import thing\n\ndef test_needs_it():\n    pass\n")
    (tmp_path / "tests/test_mixed.py").write_text(
        "import pytest\n\n"
        "class TestC:\n    def test_red(self):\n        assert 0\n\n"
        "@pytest.mark.parametrize('v', [1, 2])\ndef test_half(v):\n    assert v == 1\n\n"
        "def test_green():\n    pass\n\n"
        "def test_skipped():\n    pytest.skip('not here')\n\n"
        "def test_not_asked_for():\n    assert 0\n"
    )
    ids = [
        "tests/test_gone.py::test_needs_it",
        "tests/test_mixed.py::TestC::test_red",
        "tests/test_mixed.py::test_half",
        "tests/test_mixed.py::test_green",
        "tests/test_mixed.py::test_skipped",
        "tests/test_mixed.py::test_never_collected",
    ]
    got = red_first.outcomes(ids, tmp_path, python=[sys.executable])
    assert got == {
        "tests/test_gone.py::test_needs_it": "failed",
        "tests/test_mixed.py::TestC::test_red": "failed",
        "tests/test_mixed.py::test_half": "failed",
        "tests/test_mixed.py::test_green": "passed",
        "tests/test_mixed.py::test_skipped": "skipped",
        "tests/test_mixed.py::test_never_collected": "skipped",
    }


def test_the_summary_is_also_written_for_the_checks_page(tmp_path, monkeypatch):
    page = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(page))
    red_first.publish(["nothing to prove: no new or changed tests"])
    assert "nothing to prove" in page.read_text()


def test_a_new_script_and_its_test_are_red_before_the_change_and_green_on_the_branch(tmp_path):
    """Red-first once failed this way on its own pull request. The plugin was loaded from the
    branch's scripts folder, under this script's own name, so the old code's tests imported it.
    The fake pull request adds a script under that same name, which then could not be told apart."""
    repo = tmp_path / "repo"
    (repo / "tests").mkdir(parents=True)
    (repo / "scripts").mkdir()
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.email", "a@b.c")
    git(repo, "config", "user.name", "A")
    (repo / "scripts/old.py").write_text("OLD = 1\n")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "base")
    base = git(repo, "rev-parse", "HEAD").strip()
    (repo / "scripts/red_first.py").write_text("ANSWER = 42\n")
    (repo / "tests/test_new_script.py").write_text(
        "import pathlib\nimport sys\n\n"
        "sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / 'scripts'))\n\n"
        "import red_first\n\n\ndef test_it_answers():\n    assert red_first.ANSWER == 42\n"
    )
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "head")
    head = git(repo, "rev-parse", "HEAD").strip()

    ids = ["tests/test_new_script.py::test_it_answers"]
    before, after = red_first.run_both(base, head, cwd=repo, setup=lambda tree: [sys.executable])(ids)
    assert before == {ids[0]: "failed"}
    assert after == {ids[0]: "passed"}


def test_a_run_leaves_no_checkout_folder_behind(tmp_path, monkeypatch):
    """Each run checks out two trees in a folder of its own, and must take the folder away too.

    It once removed the trees and kept their empty folder, so every run on this machine left a
    red-first-* folder in the temporary folder, and they piled up by the hundred.
    """
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.setenv("RUNNER_TEMP", str(scratch))
    repo, base, head = fake_pull_request(
        tmp_path, {"tests/test_a.py": "def test_a():\n    pass\n"}, {"tests/test_a.py": "def test_a():\n    assert 1\n"}
    )
    red_first.run_both(base, head, cwd=repo, setup=lambda tree: [sys.executable])(["tests/test_a.py::test_a"])
    assert list(scratch.iterdir()) == []


def test_a_run_that_fails_also_leaves_no_checkout_folder_behind(tmp_path, monkeypatch):
    """A before-tree that can't be built stops the run, and the folder still goes."""
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.setenv("RUNNER_TEMP", str(scratch))
    repo, base, head = fake_pull_request(
        tmp_path, {"tests/test_a.py": "def test_a():\n    pass\n"}, {"tests/test_b.py": "def test_b():\n    pass\n"}
    )
    monkeypatch.setattr(red_first, "before_tree", lambda *a, **k: (_ for _ in ()).throw(SystemExit("no tree")))
    with pytest.raises(SystemExit):
        red_first.run_both(base, head, cwd=repo, setup=lambda tree: [sys.executable])(["tests/test_a.py::test_a"])
    assert list(scratch.iterdir()) == []


def test_a_test_marked_to_fail_that_passes_does_not_count_as_passed(tmp_path):
    """pytest calls an expected failure that passes a pass. Its mark says it is not meant to."""
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/test_x.py").write_text(
        "import pytest\n\n\n@pytest.mark.xfail(reason='not yet')\ndef test_x():\n    pass\n"
    )
    got = red_first.outcomes(["tests/test_x.py::test_x"], tmp_path, python=[sys.executable])
    assert got == {"tests/test_x.py::test_x": "skipped"}


def fake_pull_request(tmp_path, base_files, head_files):
    """A repository with a base commit and a head commit; gives the folder and both hashes."""
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.email", "a@b.c")
    git(repo, "config", "user.name", "A")
    hashes = []
    for files in (base_files, head_files):
        for name, text in files.items():
            (repo / name).parent.mkdir(parents=True, exist_ok=True)
            (repo / name).write_text(text)
        git(repo, "add", ".")
        git(repo, "commit", "-q", "-m", "a commit")
        hashes.append(git(repo, "rev-parse", "HEAD").strip())
    return repo, *hashes


CONFTEST_NEEDING_HELPER = (
    "import pathlib\nimport sys\n\n"
    "sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / 'scripts'))\n\n"
    "import helper  # noqa: F401\n"
)


def test_a_run_that_never_reports_is_said_with_pytest_s_exit_code(tmp_path):
    """A conftest that cannot import stops pytest before any test runs; that is not 'skipped'."""
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/conftest.py").write_text("import nowhere  # noqa: F401\n")
    (tmp_path / "tests/test_a.py").write_text("def test_a():\n    pass\n")
    with pytest.raises(red_first.Unreported) as stopped:
        red_first.outcomes(["tests/test_a.py::test_a"], tmp_path, python=[sys.executable])
    assert stopped.value.code not in (0, 1)


def test_a_branch_run_that_never_reports_fails_the_check_and_says_what_to_do(tmp_path):
    test = "def test_a():\n    pass\n"
    repo, base, head = fake_pull_request(
        tmp_path,
        {"tests/test_old.py": test, "scripts/helper.py": "HELP = 1\n"},
        {"tests/test_a.py": test, "tests/conftest.py": "import nowhere  # noqa: F401\n"},
    )
    run = red_first.run_both(base, head, cwd=repo, setup=lambda tree: [sys.executable])
    with pytest.raises(SystemExit) as stopped:
        run(["tests/test_a.py::test_a"])
    said = str(stopped.value)
    assert "on the branch" in said and "exit code" in said and "push again" in said


def test_a_before_tree_whose_conftest_cannot_import_counts_every_test_as_failed(tmp_path, capsys):
    test = "def test_a():\n    pass\n"
    repo, base, head = fake_pull_request(
        tmp_path,
        {"tests/test_old.py": test, "scripts/other.py": "OTHER = 1\n"},
        {"tests/test_a.py": test, "tests/conftest.py": CONFTEST_NEEDING_HELPER, "scripts/helper.py": "HELP = 1\n"},
    )
    ids = ["tests/test_a.py::test_a"]
    before, after = red_first.run_both(base, head, cwd=repo, setup=lambda tree: [sys.executable])(ids)
    assert before == {ids[0]: "failed"}
    assert after == {ids[0]: "passed"}
    assert "counts as failed" in capsys.readouterr().out


def test_a_tree_s_tests_cannot_import_the_branch_s_scripts_through_the_plugin(tmp_path):
    """The plugin is loaded from outside the tree. Were its folder the branch's scripts/,
    the old code's tests could import the branch's own scripts, and pass where they must fail."""
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/test_uses_it.py").write_text("import red_first\n\n\ndef test_uses_it():\n    pass\n")
    got = red_first.outcomes(["tests/test_uses_it.py::test_uses_it"], tmp_path, python=[sys.executable])
    assert got == {"tests/test_uses_it.py::test_uses_it": "failed"}


def test_the_check_runs_from_any_folder_given_the_repository(tmp_path, monkeypatch, capsys):
    """A session whose shell can't stay in a worktree still runs the check on it, by naming it."""
    test = (
        "import pathlib\nimport sys\n\n"
        "sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))\n\n"
        "from answer import X\n\n\ndef test_x():\n    assert X == 2\n"
    )
    repo, base, head = fake_pull_request(
        tmp_path, {"answer.py": "X = 1\n"}, {"answer.py": "X = 2\n", "tests/test_x.py": test}
    )
    real = red_first.run_both
    monkeypatch.setattr(
        red_first, "run_both", lambda b, h, cwd=None: real(b, h, cwd=cwd, setup=lambda tree: [sys.executable])
    )
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    assert red_first.main(["--repo", str(repo), "--base", base, "--head", head]) == 0
    assert "1 test failed before this change and pass on it: tests/test_x.py::test_x" in capsys.readouterr().out
