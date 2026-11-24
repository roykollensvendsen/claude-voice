"""No shell command changes what steers the next session (approver v4.1 b1, f6, f7).

The session hook reads every word of a shell command that names a path. A word under a
file that steers the next session (hooks, agents, commands, rules, output styles, the MCP
list, local instructions) is refused, and under a tracked one (skills, CLAUDE.md, AGENTS.md)
it is asked. Only a plain read passes: one reader on its own, with no option that writes or
runs. Each word is resolved through `..` and links first.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from claude_voice import session_hook, steering

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="links need privileges on Windows")


@pytest.fixture
def home(tmp_path: Path) -> Path:
    found = tmp_path / "home"
    (found / ".claude" / "projects" / "p" / "memory").mkdir(parents=True)
    return found


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    found = tmp_path / "repo"
    for folder in ("hooks", "skills/s", "agents", "worktrees/w/src"):
        (found / ".claude" / folder).mkdir(parents=True)
    (found / "x").mkdir()
    (found / "sub").mkdir()
    return found


def judge(command: str, repo: Path, home: Path) -> str | None:
    return steering.judge(command, str(repo), str(home))


DENIED = [
    ".claude/hooks/a.sh",
    ".claude/agents/a.md",
    ".claude/commands/a.md",
    ".claude/rules/a.md",
    ".claude/output-styles/a.md",
    ".claude/settings.local.json",
    ".claude",
    ".mcp.json",
    "CLAUDE.local.md",
    "sub/CLAUDE.local.md",
]
ASKED = [".claude/skills/s/SKILL.md", "CLAUDE.md", "sub/CLAUDE.md", ".claude/CLAUDE.md", "AGENTS.md"]
WRITERS = [
    "cp y {}",
    "mv y {}",
    "tee {}",
    "echo x > {}",
    "echo x >> {}",
    "sed -i s/a/b/ {}",
    "touch {}",
    "python3 w.py {}",
]


@pytest.mark.parametrize("name", DENIED)
@pytest.mark.parametrize("verb", WRITERS)
def test_a_shell_write_to_what_steers_the_next_session_is_refused(repo, home, name, verb):
    assert judge(verb.format(name), repo, home) == "deny"


@pytest.mark.parametrize("name", ASKED)
@pytest.mark.parametrize("verb", WRITERS)
def test_a_shell_write_to_a_tracked_file_that_steers_is_asked(repo, home, name, verb):
    assert judge(verb.format(name), repo, home) == "ask"


@pytest.mark.parametrize("name", ["skills/s/SKILL.md", "CLAUDE.md", "agents/a.md", "commands/a.md", "rules/a.md"])
@pytest.mark.parametrize("form", ["~/.claude/{}", "$HOME/.claude/{}", "{home}/.claude/{}"])
def test_every_steering_file_at_home_is_refused(repo, home, name, form, monkeypatch):
    monkeypatch.setenv("HOME", str(home))
    assert judge("cp y " + form.format(name, home=home), repo, home) == "deny"


@pytest.mark.parametrize(
    "command",
    [
        "find .claude -delete",
        "find .claude/hooks -name a -exec sh {} ;",
        "sed -n p .claude/hooks/a.sh",
        "rm .mcp.json",
        "cd .claude/hooks",
        "chmod +x .claude/hooks/a.sh",
        "ln -s /tmp/a .claude/agents/a.md",
        "cp y .claude/hoo*/a.sh",
        "cp y .*",
        "cp -r y .claude/{hooks,agents}",
    ],
)
def test_other_forms_that_reach_a_locked_path_are_refused(repo, home, command):
    assert judge(command, repo, home) == "deny"


READS = [
    "cat {}",
    "head -n 5 {}",
    "tail {}",
    "wc -l {}",
    "grep -n x {}",
    "rg x {}",
    "ls -la {}",
    "stat {}",
    "git diff -- {}",
    "git log -p -- {}",
    "git show HEAD -- {}",
    "git status {}",
    "git blame {}",
    "git -C . diff -- {}",
]


@pytest.mark.parametrize("name", DENIED + ASKED)
@pytest.mark.parametrize("read", READS)
def test_a_plain_read_of_a_locked_path_passes(repo, home, name, read):
    assert judge(read.format(name), repo, home) is None


@pytest.mark.parametrize(
    "command",
    [
        "git diff --output=o -- {}",
        "git log --output o -- {}",
        "git show --ext-diff -- {}",
        "git diff --textconv -- {}",
        "git log -o -- {}",
        "rg --pre sh x {}",
        "rg --pre-glob '*' x {}",
        "git -c core.pager=sh log -- {}",
    ],
)
def test_a_reader_s_option_that_writes_or_runs_is_refused(repo, home, command):
    assert judge(command.format(".claude/hooks/a.sh"), repo, home) == "deny"
    assert judge(command.format("CLAUDE.md"), repo, home) == "ask"


@pytest.mark.parametrize(
    "command",
    [
        "cat {} > o",
        "cat {} | sh",
        "cat {}; true",
        "cat {} && true",
        "cat {} || true",
        "cat $(echo {})",
        "cat `echo {}`",
        "cat <(cat {})",
        "xargs cat {}",
        "env cat {}",
        "command cat {}",
        "exec cat {}",
        "nohup cat {}",
        "sh -c 'cat {}'",
        "bash -c 'cat {}'",
        "less {}",
        "file {}",
    ],
)
def test_a_reader_that_writes_runs_or_is_wrapped_is_refused(repo, home, command):
    assert judge(command.format(".claude/hooks/a.sh"), repo, home) == "deny"
    assert judge(command.format("CLAUDE.md"), repo, home) == "ask"


@pytest.mark.parametrize("shell", ["sh -c", "bash -c", "env bash -c", "sudo sh -c", "eval"])
def test_a_command_handed_to_a_shell_as_one_word_is_judged_too(repo, home, shell):
    assert judge(f"{shell} 'cp y .claude/hooks/a.sh'", repo, home) == "deny"
    assert judge(f"{shell} 'cp y CLAUDE.md'", repo, home) == "ask"
    assert judge(f"{shell} 'cp y x/a.md'", repo, home) is None


@pytest.mark.parametrize(
    ("word", "said"),
    [
        (".claude/hoo*/a.sh", "deny"),
        (".claude/{hooks,agents}/a.sh", "deny"),
        (".*", "deny"),
        ("CLAUDE.m?", "ask"),
        ("$X/CLAUDE.md", "ask"),
        ("$X/.mcp.json", "deny"),
        ("x/*", None),
        # brace expansion: each word bash makes is judged
        (".{c,}laude/hooks/a.sh", "deny"),
        (".{c{l,x},z}aude/hooks/a.sh", "deny"),
        (".cl{a..a}ude/hooks/a.sh", "deny"),
        ("{.,}mcp.json", "deny"),
        ("{CLAUDE,README}.md", "ask"),
        ("{a,b}.txt", None),
    ],
)
def test_a_word_the_shell_would_change_is_judged_by_every_name_it_can_reach(repo, home, word, said):
    (repo / "CLAUDE.md").write_text("x", encoding="utf-8")
    assert judge(f"cp y {word}", repo, home) == said


def test_a_plain_read_of_braces_in_a_plain_folder_passes(repo, home):
    assert judge("ls {a,b}.txt", repo, home) is None


@pytest.mark.parametrize("word", ["{1..100000}", "x{a..z}{a..z}", "{0..9}{0..9}{0..9}"])
def test_a_brace_the_hook_cannot_expand_counts_as_locked(repo, home, word):
    assert judge(f"cp y {word}", repo, home) == "deny"


@pytest.mark.parametrize("word", ["${X:-.cla}ude/hooks/a.sh", "${X:=.claude}/hooks/a", "${X/y/.claude}", "${#X}"])
def test_a_parameter_with_a_default_or_any_other_operator_counts_as_locked(repo, home, word):
    assert judge(f"cp y {word}", repo, home) == "deny"


@pytest.mark.parametrize("word", ["$'.cl\\x61ude/hooks/a'", "$'.cl\\141ude/hooks/a'", "$'.cl\\u0061ude/hooks/a'"])
def test_a_path_spelled_in_the_shell_s_own_quoting_is_judged_as_bash_reads_it(repo, word):
    given = {"tool_name": "Bash", "tool_input": {"command": f"cp y {word}"}, "cwd": str(repo), "session_id": "s"}
    assert session_hook.decision(given, tmp_root=Path("/nonexistent"), repos=[], terms=None) == "deny"


@posix_only
def test_a_path_is_judged_both_as_written_and_as_resolved(repo, home, tmp_path):
    elsewhere = tmp_path / "elsewhere" / "s"
    elsewhere.mkdir(parents=True)
    (home / ".claude" / "skills").mkdir()
    os.symlink(elsewhere, home / ".claude" / "skills" / "s")
    assert judge("cp x ~/.claude/skills/s/SKILL.md", repo, home) == "deny"
    # in a clone, a steering folder linked out to a plain folder: only the path as written is locked
    plain = tmp_path / "plain"
    plain.mkdir()
    os.symlink(plain, repo / ".claude" / "commands")
    assert judge("cp y .claude/commands/a.md", repo, home) == "deny"


@posix_only
def test_what_a_link_under_claude_points_to_is_refused_too(repo, home, tmp_path):
    elsewhere = tmp_path / "elsewhere"
    (elsewhere / "s").mkdir(parents=True)
    (elsewhere / "hooks").mkdir()
    (home / ".claude" / "skills").mkdir()
    os.symlink(elsewhere / "s", home / ".claude" / "skills" / "s")
    os.symlink(elsewhere / "hooks", home / ".claude" / "hooks")
    assert judge(f"cp x {elsewhere}/s/SKILL.md", repo, home) == "deny"
    assert judge(f"cd {elsewhere}/s", repo, home) == "deny"
    assert judge(f"tee {elsewhere}/hooks/a.sh", repo, home) == "deny"
    assert judge(f"tee {elsewhere}/other.md", repo, home) is None


def test_a_path_is_judged_after_its_dot_dots_are_resolved(repo, home):
    assert judge("cat ./x/../.claude/hooks/a", repo, home) is None
    assert judge("cp y ./x/../.claude/hooks/a", repo, home) == "deny"
    assert judge("cp y .claude/hooks/../../.claude/../x/a", repo, home) is None


@posix_only
def test_a_path_is_judged_after_its_links_are_resolved(repo, home):
    os.symlink(repo / ".claude" / "hooks", repo / "harmless")
    assert judge("cp y harmless/a.sh", repo, home) == "deny"
    assert judge("cp y harmless", repo, home) == "deny"


def test_a_word_that_cannot_be_resolved_counts_as_locked(repo, home):
    assert judge("cp y a\x00b", repo, home) == "deny"


@pytest.mark.parametrize(
    "command",
    [
        "cp a b",
        "tee x/notes.md",
        "tee ~/.claude/projects/p/memory/note.md",
        "cp y .claude/worktrees/w/src/a.py",
        'git commit -m "edit CLAUDE.md and .claude/hooks"',
        "uv run pytest -q",
        "echo $PATH",
    ],
)
def test_what_steers_nothing_is_left_alone(repo, home, command, monkeypatch):
    monkeypatch.setenv("HOME", str(home))
    assert judge(command, repo, home) is None


def test_the_session_hook_refuses_a_locked_path(repo, monkeypatch):
    given = {"tool_name": "Bash", "tool_input": {"command": "tee .claude/hooks/a"}, "cwd": str(repo), "session_id": "s"}
    assert session_hook.decision(given, tmp_root=Path("/nonexistent"), repos=[], terms=None) == "deny"
    given["tool_input"] = {"command": "tee CLAUDE.md"}
    assert session_hook.decision(given, tmp_root=Path("/nonexistent"), repos=[], terms=None) == "ask"
