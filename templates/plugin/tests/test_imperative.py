"""What the imperative rule accepts and refuses.

The linter this repository uses has the setting and enforces nothing, so this
is the mechanism. Every case below is one somebody will write.
"""

import functools
import pathlib
import subprocess
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / "scripts"))

import imperative


@pytest.mark.parametrize(
    "subject",
    [
        "docs: Add the check",
        "feat: Write the walking skeleton",
        "fix: Compare the gate, not only the tool",
        "ci: Run the gates this repository claimed to have",
        "docs: Address the reviewer's finding",  # ends in -ss and is imperative
        "chore: Read the tag from Git",  # ends in -ed and is imperative
        "feat: Bring the assets in",  # ends in -ing and is imperative
        "docs: Process every page",  # ends in -s and is imperative
    ],
)
def test_an_imperative_subject_passes(subject):
    assert imperative.offence(subject) is None, subject


# Two rules share this file, and a mutation kill is only evidence about the rule
# whose own test went red. So a subject that is a verb in the wrong shape and a
# subject that is not a verb at all are tested apart, under the rules' own names.
@pytest.mark.parametrize(
    ("subject", "why"),
    [
        ("docs: Added the check", "past tense"),
        ("docs: Adds the check", "third person"),
        ("docs: Adding the check", "gerund"),
        ("fix: Fixed a bug", "past tense"),
        ("fix: Fixes a bug", "third person"),
        ("chore: Bumped the version", "past tense"),
        ("docs: Wrote the guide", "past tense"),
        ("refactor: Made the check smaller", "past tense"),
        ("test: Removes the flag", "third person"),
    ],
)
def test_a_subject_in_the_past_tense_is_refused(subject, why):
    found = imperative.offence(subject)
    assert found is not None, f"{subject} should have been refused as a {why}"


@pytest.mark.parametrize(
    ("subject", "why"),
    [
        ("feat: A walking skeleton, green in CI", "noun phrase"),
        ("docs: What each claim rests on", "a question"),
        ("docs: The rules the check enforces", "noun phrase"),
    ],
)
def test_a_subject_that_opens_a_noun_phrase_is_refused(subject, why):
    found = imperative.offence(subject)
    assert found is not None, f"{subject} should have been refused as a {why}"


def test_a_subject_with_a_scope_is_read_past_it():
    assert imperative.offence("feat(cli): Added a verb") is not None
    assert imperative.offence("feat(cli)!: Add a verb") is None


def test_the_range_is_read_from_git(tmp_path):
    """A rule that cannot see the commits enforces nothing.

    This built its own history after reading the checkout's cost a green build:
    `HEAD~1..HEAD` is one commit on a push and two on a pull request, because a
    pull request is checked out as a merge of the branch and its base. A test
    that reads the repository it happens to be running in is a test whose answer
    depends on how it was started.
    """
    run = functools.partial(subprocess.run, cwd=tmp_path, check=True, capture_output=True)
    run(["git", "init", "-q", "-b", "main"])
    run(["git", "config", "user.email", "test@example.com"])
    run(["git", "config", "user.name", "A Test"])
    for subject in ("feat: Add the first thing", "fix: Repair the second thing"):
        run(["git", "commit", "-q", "--allow-empty", "-m", subject])

    found = imperative.subjects("HEAD~1..HEAD", cwd=str(tmp_path))
    assert [subject for _, subject in found] == ["fix: Repair the second thing"]


def test_a_message_file_is_read_the_way_the_hook_sees_it(tmp_path):
    """The commit-msg hook passes a file, not a range, and comments come first."""
    message = tmp_path / "COMMIT_EDITMSG"
    message.write_text("# Please enter the commit message\n\ndocs: Added a thing\n\nA body.\n")
    assert imperative.from_file(str(message)) == [("COMMIT_EDITMSG", "docs: Added a thing")]


def test_an_empty_message_file_offends_nothing(tmp_path):
    message = tmp_path / "COMMIT_EDITMSG"
    message.write_text("# only comments\n")
    assert imperative.from_file(str(message)) == []


def test_every_commit_means_every_commit_including_the_first():
    """A range excludes its own start, so root..HEAD silently skips the root."""
    root = subprocess.run(
        ["git", "rev-list", "--max-parents=0", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()
    every = {sha for sha, _ in imperative.subjects("HEAD")}
    as_a_range = {sha for sha, _ in imperative.subjects(f"{root}..HEAD")}
    assert root[:7] in every
    assert root[:7] not in as_a_range
