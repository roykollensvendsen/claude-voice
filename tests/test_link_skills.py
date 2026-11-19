"""Skills live in skills/, and each folder in .claude/skills/ is a committed link to its source."""

import importlib.util
import os
import pathlib
import subprocess

import pytest

ROOT = pathlib.Path(__file__).parent.parent


@pytest.fixture
def linker():
    """The script, loaded by path: scripts/ is not a package."""
    spec = importlib.util.spec_from_file_location("link_skills", ROOT / "scripts/link_skills.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _git(root, *args):
    subprocess.run(
        ["git", "-c", "core.hooksPath=/dev/null", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
        cwd=root,
        check=True,
        capture_output=True,
    )


@pytest.fixture
def clone(tmp_path):
    """A repository with one skill committed in skills/, and its older copy committed in .claude/skills/."""
    _git(tmp_path, "init", "-q")
    source = tmp_path / "skills/demo"
    source.mkdir(parents=True)
    (source / "SKILL.md").write_text("# demo, newer\n")
    (source / "rules.md").write_text("rules\n")
    copy = tmp_path / ".claude/skills/demo"
    copy.mkdir(parents=True)
    (copy / "SKILL.md").write_text("# demo\n")
    (copy / "rules.md").write_text("rules\n")
    _git(tmp_path, "add", "skills", ".claude")
    _git(tmp_path, "commit", "-q", "-m", "skills")
    return tmp_path


def test_every_installed_skill_is_a_relative_link_to_its_source():
    """What Claude Code reads is the source itself, in every clone and worktree."""
    installed = ROOT / ".claude/skills"
    sources = sorted(p.parent.name for p in (ROOT / "skills").glob("*/SKILL.md"))
    assert sources, "skills/ holds no skill"
    assert sorted(p.name for p in installed.iterdir()) == sources
    wrong = [p.name for p in installed.iterdir() if not p.is_symlink() or os.readlink(p) != f"../../skills/{p.name}"]
    assert wrong == [], "make these links with scripts/link_skills.py: " + ", ".join(wrong)


def test_a_plain_copy_becomes_a_relative_link(clone, linker, capsys):
    assert linker.main([], root=clone) == 0
    place = clone / ".claude/skills/demo"
    assert place.is_symlink() and os.readlink(place) == "../../skills/demo"
    assert (place / "rules.md").read_text() == "rules\n"
    assert linker.main([], root=clone) == 0, "a second run changes nothing"
    assert "linked .claude/skills/demo -> ../../skills/demo" in capsys.readouterr().out


def test_a_skill_folder_holding_anything_not_committed_is_refused(clone, linker, capsys):
    (clone / ".claude/skills/demo/notes.md").write_text("mine\n")
    (clone / ".claude/skills/demo/rules.md").write_text("changed\n")
    assert linker.main([], root=clone) == 1
    out = capsys.readouterr().out
    assert ".claude/skills/demo/notes.md" in out and ".claude/skills/demo/rules.md" in out
    assert "Nothing was changed." in out
    assert not (clone / ".claude/skills/demo").is_symlink()
    assert (clone / ".claude/skills/demo/notes.md").read_text() == "mine\n"


def test_a_skill_that_lives_only_in_claude_is_refused(clone, linker, capsys):
    (clone / ".claude/skills/stray").mkdir()
    (clone / ".claude/skills/stray/SKILL.md").write_text("# stray\n")
    assert linker.main([], root=clone) == 1
    assert ".claude/skills/stray has no source in skills/" in capsys.readouterr().out
    assert not (clone / ".claude/skills/demo").is_symlink()


def test_a_link_that_points_anywhere_but_its_own_source_is_refused(clone, linker, capsys):
    place = clone / ".claude/skills/demo"
    for f in place.iterdir():
        f.unlink()
    place.rmdir()
    place.symlink_to("/tmp", target_is_directory=True)
    assert linker.main([], root=clone) == 1
    assert "links to /tmp, not ../../skills/demo" in capsys.readouterr().out


def test_nothing_is_changed_while_anything_is_refused(clone, linker):
    (clone / "skills/other").mkdir()
    (clone / "skills/other/SKILL.md").write_text("# other\n")
    _git(clone, "add", "skills/other")
    (clone / ".claude/skills/other").mkdir()
    (clone / ".claude/skills/other/SKILL.md").write_text("# made here, never committed\n")
    assert linker.main([], root=clone) == 1
    assert not (clone / ".claude/skills/demo").is_symlink(), "the clean one waits for the refused one"


def test_check_changes_nothing_and_names_each_copy(clone, linker, capsys):
    assert linker.main(["--check"], root=clone) == 1
    assert ".claude/skills/demo is not a link yet" in capsys.readouterr().out
    assert not (clone / ".claude/skills/demo").is_symlink()
