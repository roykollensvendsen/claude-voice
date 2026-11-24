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
    found = ci_limits.scan(tmp_path)
    assert found == [".github/workflows/checks.yml:11: the checks job runs tests and has timeout-minutes"]


def test_a_job_that_runs_no_tests_may_keep_its_time_limit(tmp_path):
    workflow(tmp_path, CAPPED.replace("timeout-minutes: 20", "permissions: {}"))
    assert ci_limits.scan(tmp_path) == []


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
    assert ci_limits.scan(tmp_path) == []


def test_a_checkout_without_workflows_has_nothing_to_check(tmp_path):
    assert ci_limits.scan(tmp_path) == []


def test_ci_limits_fails_and_names_each_place(tmp_path, capsys):
    workflow(tmp_path, CAPPED)
    assert main(["ci-limits", "--root", str(tmp_path)]) == 1
    assert "checks.yml:11: the checks job runs tests" in capsys.readouterr().out


def test_ci_limits_passes_a_clean_tree(tmp_path, capsys):
    workflow(tmp_path, CAPPED.replace("timeout-minutes: 20", "permissions: {}"))
    assert main(["ci-limits", "--root", str(tmp_path)]) == 0
    assert "no job that runs tests has a time limit" in capsys.readouterr().out
