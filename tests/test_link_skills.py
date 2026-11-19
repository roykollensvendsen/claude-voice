"""Skills live in skills/, and each entry in .claude/skills/ is a committed link to its source."""

import pathlib
import subprocess

ROOT = pathlib.Path(__file__).parent.parent


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, check=True, capture_output=True, text=True).stdout


def test_every_installed_skill_is_a_committed_relative_link_to_its_source():
    """What Claude Code reads is the source itself, in every clone and worktree. Read from what git
    holds, so a checkout that still has the old folders on disk is judged by what it commits."""
    sources = sorted(p.parent.name for p in (ROOT / "skills").glob("*/SKILL.md"))
    assert sources, "skills/ holds no skill"
    entries = {}
    for line in _git("ls-files", "-s", "--", ".claude/skills").splitlines():
        info, path = line.split("\t", 1)
        mode, blob = info.split()[:2]
        entries[path] = (mode, blob)
    assert sorted(entries) == [f".claude/skills/{name}" for name in sources], (
        ".claude/skills/ must hold one link per skill in skills/ and nothing else"
    )
    wrong = []
    for name in sources:
        mode, blob = entries[f".claude/skills/{name}"]
        target = _git("cat-file", "-p", blob)
        if mode != "120000" or target != f"../../skills/{name}":
            wrong.append(f"{name} ({mode} -> {target!r})")
    assert wrong == [], "commit these as links to ../../skills/<name>: " + ", ".join(wrong)


def test_this_checkout_has_its_skills_as_links_on_disk():
    """A checkout where git could not make links (Windows without Developer Mode) holds plain
    files instead, and Claude Code finds no skills. The doctor's check says how to fix it."""
    from claude_voice import doctor

    links = doctor.skill_links(ROOT)
    assert links["ok"], f"{links['detail']}. {links['fix']}"
