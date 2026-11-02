"""No commit message names a Claude session: those links point into one person's account."""

import pathlib
import subprocess
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / "scripts"))

import no_session_links


@pytest.mark.parametrize(
    "message",
    [
        "feat: Add a thing\n\nBody.\n\nClaude-Session: https://claude.ai/code/session_01Ab\n",
        "fix: Mend it\n\nclaude-session: https://claude.ai/code/session_01Ab\n",  # however it is written
        "docs: Note\n\nSee https://claude.ai/code/session_01AbCd for how it went.\n",
    ],
)
def test_a_commit_message_never_links_to_a_claude_session(message):
    assert no_session_links.offence(message)


@pytest.mark.parametrize(
    "message",
    [
        "feat: Add a thing\n\nCo-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>\n",
        "docs: Explain the claude.ai connector\n\nThe app reaches the bridge through claude.ai.\n",
    ],
)
def test_attribution_and_ordinary_mentions_pass(message):
    assert no_session_links.offence(message) is None


def git(folder, *args):
    return subprocess.run(["git", "-C", str(folder), *args], check=True, capture_output=True, text=True).stdout


def test_a_range_names_every_commit_that_links_a_session(tmp_path):
    git(tmp_path, "init", "-q", "-b", "main")
    for message in ("chore: Start", "feat: One\n\nClaude-Session: https://claude.ai/code/session_x", "fix: Two"):
        git(tmp_path, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", message)
    found = no_session_links.offenders("HEAD~2..HEAD", cwd=str(tmp_path))
    assert [subject for _, subject in found] == ["feat: One"]
