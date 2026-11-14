"""The mutation run's report, for the checks to keep with main's run.

Every voice repository writes the same shape, so the code-health page reads one:
``{killed, total, rules: [{name, file, tests_red, own_test_red}]}``, where
``killed`` counts the rules whose own test went red. It is never committed.
"""

import importlib.util
import json
import pathlib

ROOT = pathlib.Path(__file__).parent.parent


def _mutate():
    spec = importlib.util.spec_from_file_location("mutate_report", ROOT / "scripts/mutate.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


RESULTS = [
    ("a rule", "src/claude_voice/a.py", 3, "test_a_rule", True),
    ("a lucky rule", "src/claude_voice/b.py", 1, "test_a_lucky_rule", False),
    ("an unguarded rule", "src/claude_voice/b.py", 0, "test_an_unguarded_rule", False),
]


def test_the_report_counts_only_rules_whose_own_test_went_red():
    figures = _mutate().figures(RESULTS)
    assert (figures["killed"], figures["total"]) == (1, 3)


def test_the_report_names_each_rule_its_file_and_what_went_red():
    rules = _mutate().figures(RESULTS)["rules"]
    assert rules[1] == {"name": "a lucky rule", "file": "src/claude_voice/b.py", "tests_red": 1, "own_test_red": False}
    assert [r["name"] for r in rules] == ["a rule", "a lucky rule", "an unguarded rule"]


def test_the_report_is_written_where_the_checks_ask(tmp_path, monkeypatch):
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    to = tmp_path / "mutations.json"
    _mutate().publish(to, RESULTS)
    assert json.loads(to.read_text())["total"] == 3


def test_in_a_check_the_readable_table_goes_to_the_runs_summary(tmp_path, monkeypatch):
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    _mutate().publish(None, RESULTS)
    assert "| a lucky rule | 1 | `test_a_lucky_rule` | NO |" in summary.read_text()


def test_the_ratchet_asks_the_mutation_run_for_its_report(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("ratchet_report", ROOT / "scripts/ratchet.py")
    ratchet = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ratchet)
    assert ratchet.mutation_command(tmp_path / "m.json")[-2:] == ["--json", str(tmp_path / "m.json")]
    assert "--json" not in ratchet.mutation_command(None)
