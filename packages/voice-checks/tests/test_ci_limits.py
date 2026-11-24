"""No CI job that runs tests or mutation carries a time limit (#525, ADR-CV-021).

The owner's rule is that time caps are removed, never raised. A limit on a test job cuts a
slow but honest run short, and the red it leaves says nothing about the change.
"""

from voice_checks import ci_limits
from voice_checks.cli import main

CAPPED = """\
name: checks
on: [push]
jobs:
  commits:
    runs-on: ubuntu-latest
    timeout-minutes: 5
    steps:
      - run: uv run python scripts/imperative.py
  checks:
    runs-on: ubuntu-latest
    timeout-minutes: 20
    steps:
      - name: Tests
        run: uv run python -m pytest -q
"""


def workflow(root, text, name="checks.yml"):
    folder = root / ".github" / "workflows"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_text(text, encoding="utf-8")


def test_a_job_that_runs_tests_with_a_time_limit_is_refused(tmp_path):
    workflow(tmp_path, CAPPED)
    found = ci_limits.scan(tmp_path, frozenset({"checks.yml:commits"}))
    assert found == [".github/workflows/checks.yml:11: the checks job runs tests and has timeout-minutes"]


def test_a_job_named_as_running_no_tests_may_keep_its_time_limit(tmp_path):
    workflow(tmp_path, CAPPED.replace("timeout-minutes: 20", "permissions: {}"))
    assert ci_limits.scan(tmp_path, frozenset({"checks.yml:commits"})) == []


def test_a_time_limit_on_a_job_not_named_as_running_no_tests_is_refused(tmp_path):
    """A job that runs tests some unknown way is caught: only the named ones may keep a limit (#529)."""
    workflow(tmp_path, CAPPED.replace("timeout-minutes: 20", "permissions: {}"))
    assert ci_limits.scan(tmp_path) == [
        ".github/workflows/checks.yml:6: the commits job has timeout-minutes"
        " but is not named as running no tests (--no-tests checks.yml:commits)"
    ]


def test_a_no_test_name_covers_only_the_job_in_its_own_workflow_file(tmp_path):
    """A job with the same name in another workflow gets no pass from it."""
    lint = CAPPED.replace("timeout-minutes: 20", "permissions: {}")
    workflow(tmp_path, lint)
    workflow(tmp_path, lint, name="release.yml")
    assert ci_limits.scan(tmp_path, frozenset({"checks.yml:commits"})) == [
        ".github/workflows/release.yml:6: the commits job has timeout-minutes"
        " but is not named as running no tests (--no-tests release.yml:commits)"
    ]


def test_a_job_named_as_running_no_tests_that_runs_tests_is_refused_all_the_same(tmp_path):
    workflow(tmp_path, CAPPED)
    assert ci_limits.scan(tmp_path, frozenset({"checks.yml:commits", "checks.yml:checks"})) == [
        ".github/workflows/checks.yml:11: the checks job runs tests and has timeout-minutes"
    ]


def test_a_time_limit_on_a_step_counts_too(tmp_path):
    step = """\
jobs:
  checks:
    runs-on: ubuntu-latest
    steps:
      - name: Tests
        timeout-minutes: 30
        run: uv run pytest
"""
    workflow(tmp_path, step)
    assert ci_limits.scan(tmp_path) == [
        ".github/workflows/checks.yml:6: the checks job runs tests and has timeout-minutes"
    ]


def test_a_mutation_or_ratchet_job_counts_as_running_tests(tmp_path):
    text = """\
jobs:
  mutation:
    timeout-minutes: 60
    steps:
      - run: uv run python scripts/mutate.py
  quality:
    timeout-minutes: 60
    steps:
      - run: uv run python scripts/ratchet.py check
"""
    workflow(tmp_path, text, name="quality.yaml")
    assert [line.split(": ", 1)[1] for line in ci_limits.scan(tmp_path)] == [
        "the mutation job runs tests and has timeout-minutes",
        "the quality job runs tests and has timeout-minutes",
    ]


def test_a_comment_neither_runs_tests_nor_sets_a_limit(tmp_path):
    text = """\
jobs:
  commits:
    # No pytest here; the checks job runs it.
    timeout-minutes: 5
    steps:
      - run: echo ok
  checks:
    # timeout-minutes: 20 was here once
    steps:
      - run: uv run pytest
"""
    workflow(tmp_path, text)
    assert ci_limits.scan(tmp_path, frozenset({"checks.yml:commits"})) == []


def test_a_job_key_with_a_trailing_comment_is_still_a_job(tmp_path):
    text = """\
jobs:
  checks:  # the tests
    timeout-minutes: 20
    steps:
      - run: uv run pytest
"""
    workflow(tmp_path, text)
    assert ci_limits.scan(tmp_path) == [
        ".github/workflows/checks.yml:3: the checks job runs tests and has timeout-minutes"
    ]


def test_a_quoted_job_key_is_still_a_job(tmp_path):
    text = """\
jobs:
  commits:
    steps:
      - run: echo ok
  "checks":
    timeout-minutes: 20
    steps:
      - run: uv run pytest
"""
    workflow(tmp_path, text)
    assert ci_limits.scan(tmp_path) == [
        ".github/workflows/checks.yml:6: the checks job runs tests and has timeout-minutes"
    ]


def test_a_job_that_runs_tests_through_a_wrapper_counts_as_running_tests(tmp_path):
    wrappers = [
        "make test",
        "tox -e py312",
        "nox -s tests",
        "hatch test",
        "just test",
        "./scripts/check.sh",
        "./gradlew testDebugUnitTest",
        "npm test",
    ]
    for wrapper in wrappers:
        text = f"jobs:\n  checks:\n    timeout-minutes: 20\n    steps:\n      - run: {wrapper}\n"
        workflow(tmp_path, text)
        assert ci_limits.scan(tmp_path), wrapper


def test_a_commented_or_quoted_jobs_key_is_still_read(tmp_path):
    for jobs_line in ("jobs:  # all of them", '"jobs":'):
        text = f"{jobs_line}\n  checks:\n    timeout-minutes: 20\n    steps:\n      - run: uv run pytest\n"
        workflow(tmp_path, text)
        assert ci_limits.scan(tmp_path) == [
            ".github/workflows/checks.yml:3: the checks job runs tests and has timeout-minutes"
        ], jobs_line


def test_a_time_limit_in_a_file_whose_jobs_cannot_be_read_fails(tmp_path):
    workflow(tmp_path, "jobs: {checks: {timeout-minutes: 20, steps: [{run: pytest}]}}\n")
    assert ci_limits.scan(tmp_path) == [
        ".github/workflows/checks.yml: has timeout-minutes, but its jobs could not be read"
    ]


def test_a_checkout_without_workflows_has_nothing_to_check(tmp_path):
    assert ci_limits.scan(tmp_path) == []


def test_ci_limits_fails_and_names_each_place(tmp_path, capsys):
    workflow(tmp_path, CAPPED)
    assert main(["ci-limits", "--root", str(tmp_path)]) == 1
    assert "checks.yml:11: the checks job runs tests" in capsys.readouterr().out


def test_ci_limits_passes_a_clean_tree(tmp_path, capsys):
    workflow(tmp_path, CAPPED.replace("timeout-minutes: 20", "permissions: {}"))
    assert main(["ci-limits", "--root", str(tmp_path), "--no-tests", "checks.yml:commits"]) == 0
    assert "only jobs named as running no tests have a time limit" in capsys.readouterr().out


def test_ci_limits_takes_several_no_test_jobs(tmp_path):
    text = CAPPED.replace("uv run python -m pytest -q", "echo ok")
    workflow(tmp_path, text)
    root = ["ci-limits", "--root", str(tmp_path)]
    assert main([*root, "--no-tests", "checks.yml:commits"]) == 1
    assert main([*root, "--no-tests", "checks.yml:commits", "--no-tests", "checks.yml:checks"]) == 0
