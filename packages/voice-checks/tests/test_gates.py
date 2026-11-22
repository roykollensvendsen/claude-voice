"""The gate files: what a pull request may not change and still be merged by a session
(github-rights-v1, §4 point 4 and R4)."""

import pytest

from voice_checks import gates

ROW = '[[rule]]\nname = "a rule"\nfile = "src/x.py"\nfind = "if a:"\nreplace = "if False:"\n'


@pytest.mark.parametrize(
    "path",
    [
        ".github/workflows/checks.yml",
        "CODEOWNERS",
        "pyproject.toml",
        "packages/voice-checks/pyproject.toml",
        "uv.lock",
        "justfile",
        "committed.toml",
        "scripts/mutate.py",
        "scripts/mutations/scripts/mutate.py/a-rule.toml",
        "packages/voice-checks/scripts/mutations/src/voice_checks/docs.py/a-rule.toml",
        "packages/voice-checks/src/voice_checks/gate_files.toml",
        "src/claude_voice/guard.py",
        "config/permissions.json",
        "src/claude_voice/never_touch.py",
        "tests/conftest.py",
        "templates/plugin/tests/conftest.py",
        "pytest.ini",
        "ruff.toml",
        "packages/voice-checks/.ruff.toml",
        "mypy.ini",
        ".coveragerc",
        "evidence/quality.json",
        "sitecustomize.py",
        "src/usercustomize.py",
        ".githooks/pre-commit",
        "templates/plugin/.githooks/commit-msg",
        ".pre-commit-config.yaml",
        ".gitattributes",
        ".claude/settings.json",
        ".claude/skills/write-commit/SKILL.md",
    ],
)
def test_each_gate_file_is_a_gate(path):
    assert gates.gate(path, gates.paths()), path


@pytest.mark.parametrize(
    "path",
    [
        "README.md",
        "docs/setup.md",
        "src/claude_voice/agent.py",
        "tests/test_x.py",
        "decisions/0001.md",
        "notscripts/x.py",
        # The package's own page and tests: a session may merge those; its code it may not.
        "packages/voice-checks/README.md",
        "packages/voice-checks/tests/test_gates.py",
    ],
)
def test_an_ordinary_file_is_not_a_gate(path):
    assert not gates.gate(path, gates.paths())


def test_a_star_never_crosses_a_folder():
    assert gates.gate("a/b.py", ["a/*.py"])
    assert not gates.gate("a/c/b.py", ["a/*.py"])
    assert gates.gate("a/c/b.py", ["a/**/b.py"])
    assert gates.gate("a/b.py", ["a/**/b.py"]), "** may stand for no folder at all"


def test_a_repo_adds_its_own_gates_in_its_pyproject(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        '[tool.voice-checks.gate-files]\nadd = ["config/rules.yaml"]\n', encoding="utf-8"
    )
    found = gates.paths(tmp_path)
    assert "config/rules.yaml" in found
    assert set(gates.paths()) <= set(found), "a repo adds to the list and never takes from it"


def test_a_repo_cannot_name_a_gate_to_remove(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        '[tool.voice-checks.gate-files]\nremove = [".github/**"]\n', encoding="utf-8"
    )
    with pytest.raises(gates.GateError, match="only add"):
        gates.paths(tmp_path)


def test_a_repo_with_no_pyproject_has_the_shared_list(tmp_path):
    assert gates.paths(tmp_path) == gates.paths()


def test_a_row_whose_find_or_replace_changes_is_a_gate_change():
    """The changed .toml files of a pull request, as they were (base) and as they are (head)."""
    assert gates.row_changes([ROW], [ROW.replace("if False:", "if a:")]) == ["changes the row 'a rule'"]
    assert gates.row_changes([ROW], [ROW.replace('find = "if a:"', 'find = "if b:"')]) == ["changes the row 'a rule'"]


def test_a_row_deleted_and_kept_under_no_name_is_a_gate_change():
    assert gates.row_changes([ROW], []) == ["deletes the row 'a rule'"]
    assert gates.row_changes([ROW], [""]) == ["deletes the row 'a rule'"]
    assert gates.row_changes([ROW], ["not [toml"]) == ["makes a row file that is not valid TOML"]


def test_an_added_moved_or_unchanged_row_is_not():
    assert gates.row_changes([], [ROW]) == []
    assert gates.row_changes([ROW], [ROW + ROW.replace("a rule", "another")]) == []
    assert gates.row_changes(["[tool.x]\ny = 1\n"], []) == [], "a toml with no rows is no row file"
    # A file that moves is deleted in one place and added in another: the row is kept.
    assert gates.row_changes([ROW, ""], ["", ROW]) == []
    assert gates.row_changes([ROW], [ROW.replace("a rule", "the same rule, renamed")]) == []


def test_the_list_names_itself_so_dropping_a_line_from_it_is_a_gate_change():
    assert gates.gate("packages/voice-checks/src/voice_checks/gate_files.toml", gates.paths())


def test_the_judged_list_is_the_union_of_the_bases_and_the_heads():
    base = gates.paths_from(['[gates]\npaths = ["a/**", "b"]\n'], ['[tool.voice-checks.gate-files]\nadd = ["c"]\n'])
    head = gates.paths_from(['[gates]\npaths = ["a/**"]\n'], [""])
    assert set(base) | set(head) == {"a/**", "b", "c"}
    assert gates.paths_from(['[gates]\npaths = ["a/**"]\n'], [""], base) == sorted({"a/**", "b", "c"})
