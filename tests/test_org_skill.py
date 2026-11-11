"""The org-chart skill's helper: it checks a file the way the bridge reads it, and keeps it out of git."""

import importlib.util
import pathlib
import subprocess

import pytest

from claude_voice import org

SCRIPT = pathlib.Path(__file__).parent.parent / ".claude/skills/org-chart/scripts/org_survey.py"
EXAMPLE = pathlib.Path(__file__).parent.parent / "docs/examples/acme-robots/.claude/org.toml"


@pytest.fixture
def survey(monkeypatch, tmp_path):
    spec = importlib.util.spec_from_file_location("org_survey", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "setting", lambda: (tmp_path / "projects", []))
    monkeypatch.setattr(mod.events, "read_live", lambda _: [])
    return mod


def test_a_draft_is_checked_against_the_repository_it_will_live_in(survey, tmp_path):
    (tmp_path / "acme").mkdir()
    problems, errors = survey.check(tmp_path / "acme", EXAMPLE)
    assert "acme: component vision: folder ../acme-vision does not exist" in problems
    assert not errors
    assert org.FILE == pathlib.Path(".claude/org.toml")


def test_a_draft_with_no_code_is_an_error(survey, tmp_path):
    draft = tmp_path / "draft.toml"
    draft.write_text('[programme]\nname = "Acme"\n', encoding="utf-8")
    (tmp_path / "acme").mkdir()
    problems, errors = survey.check(tmp_path / "acme", draft)
    assert errors and problems


def test_a_repository_with_no_org_file_says_what_to_do(survey, tmp_path):
    problems, errors = survey.check(tmp_path, None)
    assert errors and "no org file found" in problems[0]


def test_the_org_file_is_excluded_from_git_once(survey, tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    assert "now excludes" in survey.exclude(tmp_path)
    assert "already excludes" in survey.exclude(tmp_path)
    lines = (tmp_path / ".git/info/exclude").read_text(encoding="utf-8").splitlines()
    assert lines.count("/.claude/org.toml") == 1


@pytest.mark.parametrize(
    ("url", "name"),
    [
        ("git@github.com:acme/arm.git", "acme/arm"),
        ("https://github.com/acme/vision", "acme/vision"),
        ("https://github.com/acme/vision.git/", "acme/vision"),
    ],
)
def test_a_remote_gives_owner_and_name(survey, url, name):
    assert survey.remote_name(url) == name


def test_repositories_that_mention_each_other_are_grouped(survey, tmp_path):
    root = tmp_path / "projects"
    a, b, c = root / "acme-robots", root / "vision", root / "garden"
    repos = {a: {"mentions": ["vision"]}, b: {"mentions": []}, c: {"mentions": []}}
    groups = survey._groups(repos, root, [])
    assert [g["repos"] for g in groups] == [sorted([str(a), str(b)])]
    assert groups[0]["why"] == ["acme-robots + vision: acme-robots's notes mention vision"]
