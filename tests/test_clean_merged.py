"""Merged branches and their worktrees are cleaned up by a checked script; unmerged work never is."""

import importlib.util
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).parent.parent


@pytest.fixture(scope="module")
def cleaner():
    """The script, loaded by path: scripts/ is not a package."""
    spec = importlib.util.spec_from_file_location("clean_merged", ROOT / "scripts/clean_merged.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["clean_merged"] = module  # a dataclass looks its module up while it is made
    spec.loader.exec_module(module)
    return module


def _git(repo, *args):
    return subprocess.run(
        ["git", "-c", "core.hooksPath=/dev/null", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


CLAUDE = "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"


def _commit(repo, name, text="x\n", *, by_claude=True):
    (repo / name).write_text(text)
    _git(repo, "add", name)
    _git(repo, "commit", "-q", "-m", f"add {name}", *(["-m", CLAUDE] if by_claude else []))


@pytest.fixture
def repo(tmp_path):
    """A repository whose main holds a rebase-merged feat/done; feat/open is not merged."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _commit(repo, "base.txt")
    _git(repo, "checkout", "-q", "-b", "feat/done")
    _commit(repo, "done.txt")
    _git(repo, "checkout", "-q", "-b", "feat/open", "main")
    _commit(repo, "open.txt")
    _git(repo, "checkout", "-q", "main")
    _commit(repo, "meanwhile.txt")  # main moved on, so the pick below is a new commit
    _git(repo, "cherry-pick", "feat/done")  # a rebase merge: same change, new commit
    assert _git(repo, "rev-parse", "main~0") != _git(repo, "rev-parse", "feat/done")
    return repo


@pytest.fixture
def never(tmp_path):
    path = tmp_path / "never-touch-branches.txt"
    path.write_text("# the owner's own branches\n")
    return path


def _branches(repo):
    return set(_git(repo, "branch", "--format=%(refname:short)").split())


def _run(cleaner, repo, never, *extra, cwds=()):
    return cleaner.main(
        ["--repo", str(repo), "--base", "main", "--never-touch", str(never), *extra], cwds=lambda: list(cwds)
    )


def test_a_branch_whose_every_commit_is_on_the_base_is_deleted(cleaner, repo, never, capsys):
    assert _run(cleaner, repo, never, "--apply") == 0
    assert "feat/done" not in _branches(repo)
    assert "delete branch feat/done" in capsys.readouterr().out


def test_git_branch_d_refuses_a_rebase_merged_branch(repo):
    """Why the script deletes with a compare-and-delete, not `git branch -d`: a rebase merge puts
    new commits on main, so the branch is never an ancestor of it, and -d refuses every one."""
    refused = subprocess.run(["git", "branch", "-d", "feat/done"], cwd=repo, capture_output=True, text=True)
    assert refused.returncode != 0 and "not fully merged" in refused.stderr


def test_a_branch_that_moved_since_it_was_judged_is_not_deleted(cleaner, repo):
    judged = _git(repo, "rev-parse", "feat/done").strip()
    _git(repo, "checkout", "-q", "feat/done")
    _commit(repo, "later.txt")
    _git(repo, "checkout", "-q", "main")
    with pytest.raises(subprocess.CalledProcessError):
        cleaner.delete_branch(repo, "feat/done", judged)
    assert "feat/done" in _branches(repo)


def test_a_branch_with_a_commit_not_on_the_base_is_kept(cleaner, repo, never, capsys):
    _run(cleaner, repo, never, "--apply")
    assert "feat/open" in _branches(repo)
    assert "keep branch feat/open: 1 commit not on main" in capsys.readouterr().out


def test_without_apply_nothing_is_changed(cleaner, repo, never, capsys):
    assert _run(cleaner, repo, never) == 0
    assert "feat/done" in _branches(repo)
    out = capsys.readouterr().out
    assert "would delete branch feat/done at " in out and ": every change on it is on main" in out


def test_the_base_branch_is_never_touched(cleaner, repo, never):
    _run(cleaner, repo, never, "--apply")
    assert "main" in _branches(repo)


def test_only_a_name_the_organisation_makes_is_cleaned(cleaner, repo, never, capsys):
    _git(repo, "branch", "my-own-idea", "feat/done")
    _git(repo, "branch", "worktree-agent-1", "feat/done")
    _run(cleaner, repo, never, "--apply")
    assert "my-own-idea" in _branches(repo) and "worktree-agent-1" not in _branches(repo)
    assert "keep branch my-own-idea: not a name the organisation makes" in capsys.readouterr().out


def test_a_branch_on_the_never_touch_list_is_kept(cleaner, repo, never, capsys):
    never.write_text("# the owner's own branches\nfeat/done\n")
    _run(cleaner, repo, never, "--apply")
    assert "feat/done" in _branches(repo)
    assert "keep branch feat/done: on the never-touch list" in capsys.readouterr().out


def test_a_branch_with_a_commit_made_by_hand_is_kept(cleaner, repo, never, capsys):
    """Such a commit was made by hand, whatever the never-touch list says."""
    _git(repo, "checkout", "-q", "-b", "fix/by-hand", "main")
    _commit(repo, "hand.txt", by_claude=False)
    _git(repo, "checkout", "-q", "main")
    _commit(repo, "between.txt")  # so the pick below is a new commit, as a rebase merge makes
    _git(repo, "cherry-pick", "fix/by-hand")
    _run(cleaner, repo, never, "--apply")
    assert "fix/by-hand" in _branches(repo)
    assert "keep branch fix/by-hand: a commit on it was made by hand" in capsys.readouterr().out


def test_a_missing_never_touch_list_stops_everything(cleaner, repo, tmp_path, capsys):
    gone = tmp_path / "nowhere.txt"
    assert _run(cleaner, repo, gone, "--apply") == 2
    assert "feat/done" in _branches(repo)
    assert str(gone) in capsys.readouterr().out


def _worktree(repo, name, branch):
    place = repo / ".claude/worktrees" / name
    _git(repo, "worktree", "add", "-q", str(place), branch)
    return place


def test_a_merged_clean_worktree_and_its_branch_are_removed(cleaner, repo, never, capsys):
    place = _worktree(repo, "done", "feat/done")
    _run(cleaner, repo, never, "--apply")
    assert not place.exists() and "feat/done" not in _branches(repo)
    out = capsys.readouterr().out
    assert f"remove worktree {place}" in out


def test_a_worktree_holding_work_not_committed_is_kept(cleaner, repo, never, capsys):
    place = _worktree(repo, "done", "feat/done")
    (place / "notes.txt").write_text("mine\n")
    _run(cleaner, repo, never, "--apply")
    assert (place / "notes.txt").exists() and "feat/done" in _branches(repo)
    assert f"keep worktree {place}: it holds work not committed" in capsys.readouterr().out


def test_a_worktree_a_program_is_working_in_is_kept(cleaner, repo, never, capsys):
    place = _worktree(repo, "done", "feat/done")
    _run(cleaner, repo, never, "--apply", cwds=[place / "sub"])
    assert place.exists() and "feat/done" in _branches(repo)
    assert f"keep worktree {place}: a program is working in it" in capsys.readouterr().out


def test_a_worktree_outside_claude_worktrees_is_never_removed(cleaner, repo, never, tmp_path, capsys):
    place = tmp_path / "elsewhere"
    _git(repo, "worktree", "add", "-q", str(place), "feat/done")
    _run(cleaner, repo, never, "--apply")
    assert place.exists() and "feat/done" in _branches(repo)
    assert "keep branch feat/done: checked out in a worktree that stays" in capsys.readouterr().out


def test_an_unmerged_worktree_is_kept(cleaner, repo, never):
    place = _worktree(repo, "open", "feat/open")
    _run(cleaner, repo, never, "--apply")
    assert place.exists() and "feat/open" in _branches(repo)


def test_a_worktree_with_any_branch_name_under_claude_worktrees_is_cleaned(cleaner, repo, never):
    _git(repo, "branch", "rebase-349", "feat/done")
    place = _worktree(repo, "rebase", "rebase-349")
    _run(cleaner, repo, never, "--apply")
    assert not place.exists() and "rebase-349" not in _branches(repo)
