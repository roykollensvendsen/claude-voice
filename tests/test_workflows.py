"""The continuous-integration workflows, checked as the text GitHub reads."""

import pathlib

ROOT = pathlib.Path(__file__).parent.parent

#: One run per workflow and ref. A newer push to a pull request cancels the run it superseded,
#: so old runs do not hold the runners; a run on main always finishes.
SUPERSEDED = """\
concurrency:
  group: ${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: ${{ github.event_name == 'pull_request' }}
"""


def test_every_pull_request_workflow_cancels_a_superseded_run_but_never_one_on_main():
    workflows = sorted((ROOT / ".github/workflows").glob("*.yml"))
    on_pull_requests = [p for p in workflows if "\n  pull_request:" in p.read_text(encoding="utf-8")]
    assert on_pull_requests, "no workflow runs on pull requests"
    missing = [p.name for p in on_pull_requests if SUPERSEDED not in p.read_text(encoding="utf-8")]
    assert missing == [], "give these the concurrency block in tests/test_workflows.py: " + ", ".join(missing)
