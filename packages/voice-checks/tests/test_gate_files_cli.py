"""`voice-checks gate-files`: the CI check that fails a pull request touching its own gates."""

import subprocess

import pytest

from voice_checks.cli import main

ROW = '[[rule]]\nname = "a rule"\nfile = "src/x.py"\nfind = "if a:"\nreplace = "if False:"\n'
GIT = ["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "-c", "commit.gpgsign=false"]


def _git(root, *args):
    return subprocess.run([*GIT, *args], cwd=root, check=True, capture_output=True, text=True, timeout=60)


@pytest.fixture
def repo(tmp_path):
    """A repo with a base commit on `main`, then a branch to change."""
    files = {
        "README.md": "# A plugin\n",
        "src/x.py": "x = 1\n",
        "scripts/mutations/src/x.py/a-rule.toml": ROW,
        "pyproject.toml": '[project]\nname = "a"\n\n[tool.voice-checks.gate-files]\nadd = ["config/**"]\n',
    }
    for name, text in files.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(text, encoding="utf-8")
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-q", "-m", "base")
    _git(tmp_path, "switch", "-q", "-c", "change")
    return tmp_path


def _commit(root, files, deleted=()):
    for name, text in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text, encoding="utf-8")
    for name in deleted:
        _git(root, "rm", "-q", name)
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "change")


def _run(root, capsys):
    status = main(["gate-files", "--root", str(root), "--base", "main"])
    return status, capsys.readouterr().out


def test_a_change_to_ordinary_files_passes(repo, capsys):
    _commit(repo, {"README.md": "# A plugin, said better\n", "src/x.py": "x = 2\n"})
    status, said = _run(repo, capsys)
    assert status == 0, said
    assert "touches no gate file" in said


def test_a_pull_request_that_touches_a_gate_file_fails_and_names_it(repo, capsys):
    _commit(repo, {".github/workflows/checks.yml": "on: push\n", "src/x.py": "x = 2\n"})
    status, said = _run(repo, capsys)
    assert status == 1
    assert ".github/workflows/checks.yml" in said
    assert "src/x.py" not in said
    assert "the owner merges this one" in said


def test_a_path_the_base_added_is_still_a_gate_when_the_head_drops_it(repo, capsys):
    """Dropping an add is itself caught: the base's list is part of the judge."""
    _commit(repo, {"pyproject.toml": '[project]\nname = "a"\n', "config/rules.yaml": "x: 1\n"})
    status, said = _run(repo, capsys)
    assert status == 1
    assert "config/rules.yaml" in said


def test_a_moved_row_file_outside_the_gate_paths_is_judged_by_its_rows(repo, capsys):
    _commit(
        repo,
        {"rows/a-rule.toml": ROW.replace("if False:", "if a:")},
        deleted=["scripts/mutations/src/x.py/a-rule.toml"],
    )
    status, said = _run(repo, capsys)
    assert status == 1
    assert "changes the row 'a rule'" in said


def test_a_base_that_cannot_be_read_fails_closed(repo, capsys):
    status = main(["gate-files", "--root", str(repo), "--base", "no-such-branch"])
    assert status == 1
    assert "could not" in capsys.readouterr().out


@pytest.mark.parametrize("name", ["ø.yml", "a b.yml", "a\tb.yml", 'a"b.yml'])
def test_a_gate_file_with_a_name_git_would_quote_still_fails_and_is_named(repo, capsys, name):
    _commit(repo, {f".github/workflows/{name}": "on: push\n"})
    status, said = _run(repo, capsys)
    assert status == 1, said
    assert f".github/workflows/{name}" in said
