"""The hook that refuses a gh command spelled with `$` or a backtick, and says why.

Design: gh-literal-deny v1.1. The settings deny refuses these commands anyway; the hook
only adds the reason, so a session can retry with literal values at once.
"""

from __future__ import annotations

import ast
import json
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from claude_voice import literal_gh

ALLOWED = [
    "gh pr merge 348 -R roykollensvendsen/claude-voice --rebase --match-head-commit 30629a7b",
    "gh pr checks 345 -R roykollensvendsen/claude-voice",
    "gh pr view 22 -R O/R --json headRefOid,files --jq '{h:.headRefOid,files:[.files[].path]}'",
    'gh api "repos/O/R/contents/x?ref=abc" -H "Accept: application/vnd.github.raw"',
    "gh pr diff 24 -R O/R | git patch-id --stable",
    "cd /tmp && gh pr view 1 -R O/R",
    "echo $X && gh pr view 1",
    'echo "done; gh is at $HOME"',
    "git commit -F - <<'EOF'\nfix: x\n\ngh pr view $N\nEOF",
    'git commit -m "fix; gh pr view $N said so"',
    "ls $HOME",
]

REFUSED = [
    "gh pr view $N -R O/R",
    'gh api "repos/$REPO/pulls"',
    "gh pr view `cat n`",
    "gh pr view 5 --json title --jq '. as $x | $x.title'",
    "for n in 1 2; do gh pr view $n; done",
    'gh pr create --body "costs $5"',
    "gh api graphql -f query='query($n:Int!){x}' -F n=5",
    "GH_REPO=O/R gh pr view $N",
    "git status\ngh pr view $N",
]


def _answer(command: str, tool: str = "Bash") -> dict:
    return literal_gh.answer({"tool_name": tool, "tool_input": {"command": command}})


@pytest.mark.parametrize("command", ALLOWED)
def test_a_gh_command_with_literal_values_is_let_through(command):
    assert _answer(command) == {}


@pytest.mark.parametrize("command", REFUSED)
def test_a_gh_command_whose_words_hold_a_dollar_or_a_backtick_is_refused_with_the_reason(command):
    out = _answer(command)["hookSpecificOutput"]
    assert out["hookEventName"] == "PreToolUse"
    assert out["permissionDecision"] == "deny"
    assert "literal values" in out["permissionDecisionReason"]
    assert "--body-file" in out["permissionDecisionReason"]


def test_a_heredoc_body_is_not_judged_as_a_command():
    assert _answer("cat <<-EOF > notes.txt\n\tgh pr view $N\n\tEOF\ngh pr view 1") == {}
    assert _answer("cat <<EOF\nx\nEOF\ngh pr view $N") != {}


def test_a_line_the_shell_cannot_parse_is_still_refused_when_a_gh_segment_holds_a_dollar():
    assert _answer("gh pr view $N --title 'unclosed") != {}
    assert _answer("echo 'unclosed && gh pr view 1") == {}


def test_a_command_with_no_gh_is_answered_at_once_without_parsing(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("parsed a command that holds no gh")

    monkeypatch.setattr(shlex, "shlex", refuse)
    assert _answer("ls $HOME | wc -l") == {}


def test_a_tool_other_than_the_shell_is_let_through():
    assert _answer("gh pr view $N", tool="Read") == {}


def _run(stdin: str) -> subprocess.CompletedProcess:
    """The hook as the owner's settings start it: the one file, by its absolute path, with
    -I -S, so neither the package's __init__.py nor any installed package runs first."""
    return subprocess.run(  # noqa: S603
        [sys.executable, "-I", "-S", str(Path(literal_gh.__file__).resolve())],
        input=stdin,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def test_the_hook_answers_claude_code_in_one_piece_of_json():
    refused = _run(json.dumps({"tool_name": "Bash", "tool_input": {"command": "gh pr view $N"}}))
    allowed = _run(json.dumps({"tool_name": "Bash", "tool_input": {"command": "gh pr view 1"}}))
    assert (refused.returncode, allowed.returncode) == (0, 0)
    assert json.loads(refused.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert json.loads(allowed.stdout) == {}


def test_input_the_hook_cannot_read_gives_no_decision():
    broken = _run("{not json")
    assert (broken.returncode, broken.stdout) == (0, "")


def test_the_hook_imports_only_the_standard_library():
    """It runs on every shell call of every session, so it must start fast and alone."""
    tree = ast.parse(Path(literal_gh.__file__).read_text(encoding="utf-8"))
    names = [a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names]
    names += [n.module or "." for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
    outside = [m for m in names if m.split(".")[0] not in sys.stdlib_module_names | {"__future__"}]
    assert not outside
