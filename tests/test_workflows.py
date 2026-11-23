"""The continuous-integration workflows, checked as the text GitHub reads."""

import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).parent.parent

#: A newer push to a pull request stops that pull request's stale run. Every other run, on
#: main, a tag or by hand, is its own group and always runs to the end: with one group per
#: ref, GitHub keeps only one waiting run, so a main commit landing between two others got none.
SUPERSEDED = """\
concurrency:
  group: ${{ github.workflow }}-${{ github.event_name == 'pull_request' && github.ref || github.run_id }}
  cancel-in-progress: ${{ github.event_name == 'pull_request' }}
"""

#: A repository that merges in batches lets pushes to main share one group (the bridge's
#: ADR-CV-023): a batch's merges land together, and only the last one's tree, the one the batch
#: proved, needs a full run. The nightly run and runs by hand stay their own group.
MAIN_PUSH = "(github.event_name == 'push' && github.ref == 'refs/heads/main')"
MAIN_PUSHES_SHARE_ONE = (
    "concurrency:\n"
    "  group: ${{ github.workflow }}-${{ github.event_name == 'pull_request' && github.ref"
    f" || {MAIN_PUSH} && 'main-push' || github.run_id }}}}\n"
    f"  cancel-in-progress: ${{{{ github.event_name == 'pull_request' || {MAIN_PUSH} }}}}\n"
)

#: The block before: one group per ref, which could skip a commit on main.
ONE_GROUP_PER_REF = """\
concurrency:
  group: ${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: ${{ github.event_name == 'pull_request' }}
"""


def started_by_pull_request(workflow: str) -> bool:
    """Whether the workflow's `on:` names pull_request or pull_request_target, in any form.

    The `on:` section runs from its own line to the next line that starts at the margin, and a
    comment in it names nothing. Taken from claude-voice-mobile's tests/test_workflow_concurrency.py.
    """
    found = re.search(r"""^["']?on["']?:(.*\n(?:[ \t-].*\n|\s*\n)*)""", workflow, re.MULTILINE)
    if not found:
        return False
    section = re.sub(r"#.*", "", found.group(1))
    return bool(re.search(r"\bpull_request(?:_target)?\b", section))


def cancels_only_a_stale_pull_request_run(workflow: str) -> bool:
    return SUPERSEDED in workflow or MAIN_PUSHES_SHARE_ONE in workflow


@pytest.mark.parametrize(
    "on",
    [
        "on:\n  pull_request:\n  push:\n",
        "on:\n  push:\n  pull_request:\n    branches: [main]\n",
        "on: pull_request\n",
        "on: [push, pull_request]\n",
        "on: [ pull_request ]\n",
        "on:\n  - push\n  - pull_request\n",
        "on:\n  pull_request_target:\n",
        '"on":\n  pull_request:\n',
    ],
    ids=["block", "block, second", "a word", "a list", "a spaced list", "a dash list", "target", "quoted on"],
)
def test_every_way_of_naming_pull_request_counts(on):
    assert started_by_pull_request(f"name: x\n{on}\njobs:\n")


@pytest.mark.parametrize(
    "on",
    [
        "on:\n  schedule:\n    - cron: '17 4 * * 1'\n  workflow_dispatch:\n",
        "on: [push]\n",
        "on:\n  push:\n    branches: [main]\n# pull_request: left out on purpose\n",
    ],
    ids=["schedule", "push only", "a comment"],
)
def test_a_workflow_without_pull_request_does_not_count(on):
    assert not started_by_pull_request(f"name: x\n{on}\njobs:\n")


def test_one_group_per_ref_is_refused_because_it_can_skip_a_commit_on_main():
    workflow = f"name: x\non:\n  push:\n  pull_request:\n\n{ONE_GROUP_PER_REF}\njobs:\n"
    assert not cancels_only_a_stale_pull_request_run(workflow)


def test_every_pull_request_workflow_cancels_a_superseded_run_and_on_main_only_a_later_main_push():
    workflows = sorted((ROOT / ".github/workflows").glob("*.yml"))
    on_pull_requests = [p for p in workflows if started_by_pull_request(p.read_text(encoding="utf-8"))]
    assert on_pull_requests, "no workflow runs on pull requests"
    missing = [p.name for p in on_pull_requests if not cancels_only_a_stale_pull_request_run(p.read_text("utf-8"))]
    assert missing == [], "give these the concurrency block in tests/test_workflows.py: " + ", ".join(missing)
