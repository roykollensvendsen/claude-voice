"""The pages read easily, and the justfile runs exactly what continuous integration runs."""

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import readable

SKIPPED = {".venv", "node_modules", ".pytest_cache"}
#: The recipes that are a gate in CI; `setup` only installs.
GATES = ("lint", "types", "private", "tests", "mutation", "commits", "red-first", "ratchet")


def _pages():
    return sorted(p for p in ROOT.rglob("*.md") if not SKIPPED & set(p.relative_to(ROOT).parts))


def test_no_sentence_on_any_page_is_over_thirty_words():
    long = [
        f"{page.relative_to(ROOT)}: {s[:60]}..." for page in _pages() for s in readable.long_sentences(page.read_text())
    ]
    assert long == [], "split these sentences: " + "; ".join(long)


def test_the_sentence_check_sees_a_long_one_and_skips_code():
    page = "# Title\n\n" + "word " * 31 + "end.\n\n```\n" + "code " * 40 + "\n```\n\n- A short item.\n"
    assert len(readable.long_sentences(page)) == 1
    assert readable.long_sentences("| " + "cell " * 20 + "| " + "cell " * 20 + "|\n") == []


def _recipes():
    """Each recipe in the justfile, by name, with its command lines."""
    recipes, name = {}, None
    for line in (ROOT / "justfile").read_text().splitlines():
        if line.startswith("    ") and name:
            recipes[name].append(line.strip())
        elif match := re.match(r"^([a-z][a-z-]*)(?:\s[^:]*)?:(?!=)", line):
            name = match.group(1)
            recipes[name] = []
    return recipes


def test_every_gate_in_the_justfile_runs_in_continuous_integration():
    """A local recipe that CI does not run, or runs otherwise, would let the two drift apart."""
    workflows = "\n".join(p.read_text() for p in sorted((ROOT / ".github/workflows").glob("*.yml")))
    recipes = _recipes()
    assert set(GATES) <= set(recipes), f"the justfile lost a gate: {set(GATES) - set(recipes)}"
    missing = []
    for gate in GATES:
        for line in recipes[gate]:
            command = line.replace("{{python}}", "uv run python").split("{{")[0]
            if command.startswith("committed"):
                if "crate-ci/committed" not in workflows or "--no-merge-commit" not in workflows:
                    missing.append(command)
            elif command not in workflows:
                missing.append(command)
    assert missing == [], "run these in .github/workflows/ as the justfile does: " + "; ".join(missing)


def test_check_runs_every_gate_a_pull_request_needs_offline():
    check = (ROOT / "justfile").read_text().split("\ncheck:", 1)[1].splitlines()[0].split()
    assert check == ["lint", "types", "private", "tests", "mutation"]


#: One run per workflow and ref. A newer push to a pull request cancels the run it superseded,
#: so old runs do not hold the runners; a run on main always finishes.
SUPERSEDED = """\
concurrency:
  group: ${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: ${{ github.event_name == 'pull_request' }}
"""


def test_every_pull_request_workflow_cancels_a_superseded_run_but_never_one_on_main():
    workflows = sorted((ROOT / ".github/workflows").glob("*.yml"))
    on_pull_requests = [p for p in workflows if "\n  pull_request:" in p.read_text()]
    assert on_pull_requests, "no workflow runs on pull requests"
    missing = [p.name for p in on_pull_requests if SUPERSEDED not in p.read_text()]
    assert missing == [], "give these the concurrency block in tests/test_docs.py: " + ", ".join(missing)


def test_no_job_that_runs_the_mutation_run_or_the_ratchet_has_a_time_limit():
    """A run cut short proves nothing, so neither is ever capped."""
    text = (ROOT / ".github/workflows/checks.yml").read_text()
    for job in ("mutation", "quality"):
        body = text.split(f"\n  {job}:\n", 1)[1].split("\n  # ", 1)[0]
        assert "timeout-minutes" not in body, job
    assert "timeout-minutes" not in (ROOT / ".github/workflows/red-first.yml").read_text()
