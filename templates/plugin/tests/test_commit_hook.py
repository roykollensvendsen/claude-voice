"""The commit-msg hook refuses, while the message is written, what CI would refuse later."""

import os
import pathlib
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).parent.parent
SCRIPTS = ("imperative.py", "no_session_links.py", "private_data_scan.py", "readable.py")


@pytest.fixture
def repo(tmp_path):
    """A scratch repository with this project's hook and the scripts it calls."""
    folder = tmp_path / "repo"
    (folder / "scripts").mkdir(parents=True)
    shutil.copytree(ROOT / ".githooks", folder / ".githooks")
    shutil.copy(ROOT / "committed.toml", folder / "committed.toml")
    for name in SCRIPTS:
        shutil.copy(ROOT / "scripts" / name, folder / "scripts" / name)
    env = {**os.environ, "HOME": str(tmp_path), "GIT_CONFIG_NOSYSTEM": "1"}
    env |= {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid"}
    env |= {"GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid"}
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=folder, env=env, check=True)
    subprocess.run(["git", "config", "core.hooksPath", ".githooks"], cwd=folder, env=env, check=True)
    return folder, env


def _commit(repo, message):
    folder, env = repo
    return subprocess.run(
        ["git", "commit", "-q", "--allow-empty", "-m", message], cwd=folder, env=env, capture_output=True, text=True
    )


def test_the_hook_lets_credit_through(repo):
    done = _commit(repo, "feat: Add a thing\n\nWhy it is needed.\n\nCo-Authored-By: A <a@example.invalid>\n")
    assert done.returncode == 0, done.stderr


def test_the_hook_refuses_another_trailer_and_says_where_it_belongs(repo):
    done = _commit(repo, "feat: Add a thing\n\nWhy it is needed.\n\nReviewed-by: B <b@example.invalid>\n")
    assert done.returncode != 0
    assert "pull request" in done.stderr


def test_the_hook_refuses_a_subject_in_the_past_tense(repo):
    assert _commit(repo, "feat: Added a thing\n").returncode != 0


def test_a_pre_commit_hook_runs_the_private_data_scan_on_the_staged_files():
    hook = ROOT / ".githooks" / "pre-commit"
    assert hook.is_file() and os.access(hook, os.X_OK)
    assert "scripts/private_data_scan.py" in hook.read_text(encoding="utf-8")


def _stage(repo, text):
    folder, env = repo
    (folder / "notes.txt").write_text(text, encoding="utf-8")
    subprocess.run(["git", "add", "notes.txt"], cwd=folder, env=env, check=True)
    return subprocess.run(
        ["git", "commit", "-q", "-m", "docs: Add notes\n"], cwd=folder, env=env, capture_output=True, text=True
    )


def test_a_staged_file_with_a_home_network_address_is_refused_before_the_commit(repo):
    done = _stage(repo, "The box answers on " + "192." + "168.1.20 at home.\n")
    assert done.returncode != 0 and "notes.txt" in done.stderr


def test_a_clean_staged_file_is_committed(repo):
    done = _stage(repo, "The box answers on 192.0.2.20 in the examples.\n")
    assert done.returncode == 0, done.stderr


def test_the_hook_lets_a_fixup_through_for_folding_in_later(repo):
    assert _commit(repo, "feat: Add a thing\n").returncode == 0
    assert _commit(repo, "fixup! feat: Add a thing\n").returncode == 0
