"""No commit message names a Claude session, and its only trailers are credit."""

import pathlib
import subprocess
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / "scripts"))

import no_session_links

#: Built from parts, so this file's own text never holds what the check refuses.
SESSION_LINE = "Claude-" + "Session: https://claude.ai/code/" + "session_01Ab"


@pytest.mark.parametrize(
    "message",
    [
        f"feat: Add a thing\n\nBody.\n\n{SESSION_LINE}\n",
        f"fix: Mend it\n\n{SESSION_LINE.lower()}\n",  # however it is written
        "docs: Note\n\nSee https://claude.ai/code/" + "session_01AbCd for how it went.\n",
    ],
)
def test_a_commit_message_never_links_to_a_claude_session(message):
    assert no_session_links.offence(message)


@pytest.mark.parametrize(
    "message",
    [
        "feat: Add a thing\n\nCo-Authored-By: Some Helper <helper@example.invalid>\n",
        "feat: Add a thing\n\nBody.\n\nSigned-off-by: A Contributor <them@example.invalid>\n",
        "feat: Add a thing\n\nco-authored-by: A <a@example.invalid>\nSigned-off-by: B <b@example.invalid>\n",
        "docs: Explain the claude.ai connector\n\nThe app reaches the bridge through claude.ai.\n",
        "fix: Mend it\n\nThe value was: wrong, and the line above says so in prose.\n",
    ],
    ids=["co-authored-by", "signed-off-by", "both, any case", "a plain mention", "prose with a colon"],
)
def test_credit_and_ordinary_text_pass(message):
    assert no_session_links.offence(message) is None


@pytest.mark.parametrize(
    "trailer",
    ["Reviewed-by: Someone <s@example.invalid>", "Ratchet-Exception: coverage - why", "Fixes: #12"],
)
def test_a_commit_carries_no_trailer_but_co_authored_by_and_signed_off_by(trailer):
    message = f"feat: Add a thing\n\nBody.\n\nCo-Authored-By: A <a@example.invalid>\n{trailer}\n"
    said = no_session_links.offence(message)
    assert said
    assert trailer.split(":")[0] in said
    assert "pull request" in said


def test_the_subject_alone_is_never_read_as_a_trailer():
    assert no_session_links.trailers("Fixes: the subject line\n") == []


def git(folder, *args):
    return subprocess.run(["git", "-C", str(folder), *args], check=True, capture_output=True, text=True).stdout


def test_a_range_names_every_commit_refused_and_why(tmp_path):
    git(tmp_path, "init", "-q", "-b", "main")
    for message in ("chore: Start", f"feat: One\n\n{SESSION_LINE}", "fix: Two\n\nFixes: #3", "fix: Three"):
        git(tmp_path, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", message)
    found = no_session_links.offenders("HEAD~3..HEAD", cwd=str(tmp_path))
    assert [subject for _, subject, _ in found] == ["fix: Two", "feat: One"]
    assert "Fixes" in found[0][2]


def test_the_hook_form_reads_a_message_file_and_drops_comments(tmp_path, monkeypatch, capsys):
    path = tmp_path / "COMMIT_EDITMSG"
    path.write_text("feat: Add a thing\n\n# Reviewed-by: a comment git drops\n", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["no_session_links.py", "--file", str(path)])
    assert no_session_links.main() == 0
    path.write_text("feat: Add a thing\n\nReviewed-by: Someone <s@example.invalid>\n", encoding="utf-8")
    assert no_session_links.main() == 1
    assert "Reviewed-by" in capsys.readouterr().err
