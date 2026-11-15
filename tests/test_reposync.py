"""`repos sync --check`: where each of the profile's repositories stands, read from local refs only.

It never fetches, never writes, and says for each folder what the acting run would do, or
why that folder is left alone. A folder is only ever fast-forwarded when nothing in it can
be lost.
"""

import hashlib
import os
import subprocess

from claude_voice import reposync
from claude_voice.profiles import Repo

GIT_ENV = {
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@example.invalid",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@example.invalid",
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
}


def git(folder, *args):
    env = {**os.environ, **GIT_ENV}
    return subprocess.run(["git", "-C", str(folder), *args], check=True, capture_output=True, text=True, env=env)


def commit(folder, name, text="x"):
    (folder / name).write_text(text)
    git(folder, "add", name)
    git(folder, "commit", "-qm", name)


def origin_and_clone(tmp_path, name="tool"):
    """A bare origin with one commit on main, and a clone of it under src/."""
    seed, bare, clone = tmp_path / "seed", tmp_path / f"{name}.git", tmp_path / "src" / name
    seed.mkdir()
    git(seed, "init", "-q", "-b", "main")
    commit(seed, "a")
    git(tmp_path, "clone", "-q", "--bare", str(seed), str(bare))
    git(tmp_path, "clone", "-q", str(bare), str(clone))
    return bare, clone


def check(tmp_path, bare, name="tool", known=None):
    [folder] = reposync.check([Repo(name, str(bare), "plugin", False)], tmp_path / "src", known or {})
    return folder


def test_a_clean_clone_on_its_default_branch_would_be_fast_forwarded(tmp_path):
    bare, _ = origin_and_clone(tmp_path)
    folder = check(tmp_path, bare)
    assert (folder.state, folder.branch, folder.held) == ("clone", "main", None)
    assert "fast-forward main" in folder.line()


def test_a_missing_folder_would_be_cloned(tmp_path):
    bare = tmp_path / "tool.git"
    folder = check(tmp_path, bare)
    assert folder.state == "missing" and f"would clone {bare}" in folder.line()


def test_a_known_folder_is_used_instead_of_one_under_the_parent(tmp_path):
    bare, clone = origin_and_clone(tmp_path)
    moved = tmp_path / "elsewhere"
    clone.rename(moved)
    assert check(tmp_path, bare, known={"tool": moved}).path == moved


def test_a_folder_that_is_not_a_clone_is_never_cloned_over(tmp_path):
    bare = tmp_path / "tool.git"
    (tmp_path / "src" / "tool").mkdir(parents=True)
    folder = check(tmp_path, bare)
    assert folder.state == "not a clone" and "left alone" in folder.line()


def test_a_folder_inside_another_repository_is_not_a_clone(tmp_path):
    bare, clone = origin_and_clone(tmp_path)
    inner = clone / "inner"
    inner.mkdir()
    assert check(tmp_path, bare, name="inner", known={"inner": inner}).state == "not a clone"


def test_a_folder_with_another_origin_is_only_reported(tmp_path):
    _, clone = origin_and_clone(tmp_path)
    folder = check(tmp_path, tmp_path / "somebody-else.git")
    assert folder.state == "other origin" and "left alone" in folder.line()
    assert str(tmp_path / "tool.git") in folder.line()


def test_the_same_repository_by_ssh_and_by_https_is_the_same_origin():
    assert reposync.same_repo("git@github.com:Someone/Tool.git", "https://github.com/someone/tool")
    assert reposync.same_repo("ssh://git@github.com/someone/tool/", "https://github.com/someone/tool.git")
    assert not reposync.same_repo("https://github.com/someone/tool", "https://github.com/someone/tool2")


def test_a_folder_with_uncommitted_or_untracked_files_is_left_alone(tmp_path):
    bare, clone = origin_and_clone(tmp_path)
    (clone / "new-file").write_text("untracked")
    folder = check(tmp_path, bare)
    assert folder.dirty and "uncommitted or untracked" in folder.held


def test_a_detached_head_is_left_alone(tmp_path):
    bare, clone = origin_and_clone(tmp_path)
    git(clone, "checkout", "-q", "--detach")
    assert "detached" in check(tmp_path, bare).held


def test_a_folder_mid_rebase_merge_cherry_pick_or_bisect_is_left_alone(tmp_path):
    bare, clone = origin_and_clone(tmp_path)
    git(clone, "bisect", "start")
    assert "bisect" in check(tmp_path, bare).held


def test_a_merge_in_progress_is_named(tmp_path):
    bare, clone = origin_and_clone(tmp_path)
    git(clone, "checkout", "-qb", "side")
    commit(clone, "a", "side")
    git(clone, "checkout", "-q", "main")
    commit(clone, "a", "main")
    subprocess.run(["git", "-C", str(clone), "merge", "side"], capture_output=True, env={**os.environ, **GIT_ENV})
    assert "merge" in check(tmp_path, bare).held


def test_only_the_default_branch_is_fast_forwarded(tmp_path):
    bare, clone = origin_and_clone(tmp_path)
    git(clone, "checkout", "-qb", "feature")
    assert "on feature, not main" in check(tmp_path, bare).held


def test_a_diverged_branch_is_left_alone(tmp_path):
    bare, clone = origin_and_clone(tmp_path)
    other = tmp_path / "other"
    git(tmp_path, "clone", "-q", str(bare), str(other))
    commit(other, "b")
    git(other, "push", "-q", "origin", "main")
    git(clone, "fetch", "-q")
    commit(clone, "c")
    folder = check(tmp_path, bare)
    assert (folder.ahead, folder.behind) == (1, 1)
    assert "diverged" in folder.held


def test_behind_is_read_from_the_last_fetch_only(tmp_path):
    bare, clone = origin_and_clone(tmp_path)
    other = tmp_path / "other"
    git(tmp_path, "clone", "-q", str(bare), str(other))
    commit(other, "b")
    git(other, "push", "-q", "origin", "main")
    assert check(tmp_path, bare).behind == 0  # not fetched, so not seen
    git(clone, "fetch", "-q")
    assert check(tmp_path, bare).behind == 1


def test_a_repos_check_takes_no_git_lock(tmp_path):
    """`git status` refreshes a stale index and writes it back unless told not to."""
    bare, clone = origin_and_clone(tmp_path)
    os.utime(clone / "a", (1, 1))
    index = clone / ".git" / "index"
    before = hashlib.sha256(index.read_bytes()).hexdigest()
    check(tmp_path, bare)
    assert hashlib.sha256(index.read_bytes()).hexdigest() == before


def test_the_commit_hook_setting_is_read(tmp_path):
    bare, clone = origin_and_clone(tmp_path)
    (clone / ".githooks").mkdir()
    assert check(tmp_path, bare).hooks == (True, None)
    git(clone, "config", "core.hooksPath", ".githooks")
    assert check(tmp_path, bare).hooks == (True, ".githooks")
