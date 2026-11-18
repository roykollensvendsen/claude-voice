"""The bridge runs git in folders a session can write to. A repository's own settings can name
programs for git to start, so every git the bridge runs switches off what it can and is skipped
in a repository whose own settings name a program anyway."""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest

from claude_voice import config, gitstatus, reposync, routing, safe_git, session_hook, software, updater, work

SRC = Path(__file__).resolve().parents[1] / "src" / "claude_voice"

needs_sh = pytest.mark.skipif(sys.platform == "win32", reason="the marker program is a shell script")

#: Every setting measured to start a program, plus two made-up keys of the same shape.
PLANTED = [
    ("core", "fsmonitor"),
    ('filter "f"', "clean"),
    ('filter "f"', "smudge"),
    ('filter "f"', "process"),
    ('diff "d"', "textconv"),
    ('diff "d"', "command"),
    ("diff", "external"),
    ('merge "m"', "driver"),
    ("core", "sshCommand"),
    ('credential "https://x"', "helper"),
    ("core", "askPass"),
    ("gpg", "program"),
    ("core", "pager"),
]


def _git(*args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args], check=True, capture_output=True
    )


def _marker_program(tmp_path: Path) -> tuple[Path, Path]:
    marker, program = tmp_path / "marker", tmp_path / "mark.sh"
    program.write_text(f'#!/bin/sh\necho ran >> "{marker}"\n')
    program.chmod(0o755)
    return marker, program


def _repo(tmp_path: Path) -> Path:
    """A clone with a tracked file, an upstream one commit ahead, and attributes naming drivers."""
    upstream, repo = tmp_path / "upstream", tmp_path / "repo"
    upstream.mkdir()
    (upstream / ".gitattributes").write_text("*.txt filter=f diff=d merge=m\n")
    (upstream / "a.txt").write_text("a\n")
    _git("init", "-q", "-b", "main", str(upstream))
    _git("-C", str(upstream), "add", ".")
    _git("-C", str(upstream), "commit", "-q", "-m", "one")
    _git("clone", "-q", str(upstream), str(repo))
    (upstream / "a.txt").write_text("a\nb\n")
    _git("-C", str(upstream), "commit", "-q", "-am", "two")
    stamp = (repo / "a.txt").stat().st_mtime + 100  # git must read the file again, so a filter runs
    os.utime(repo / "a.txt", (stamp, stamp))
    return repo


def _plant(tmp_path: Path, repo: Path, how: str) -> Path:
    """Name the marker program in every setting, the way `how` says, and in two hooks."""
    marker, program = _marker_program(tmp_path)
    planted = "".join(f"[{section}]\n\t{key} = {program}\n" for section, key in PLANTED)
    settings = repo / ".git" / "config"
    if how == "set directly":
        settings.write_text(settings.read_text() + planted)
    else:
        extra = tmp_path / "extra.config"
        extra.write_text(planted)
        head = "[include]" if how == "through include.path" else '[includeIf "gitdir:**/repo/"]'
        settings.write_text(settings.read_text() + f"{head}\n\tpath = {extra}\n")
    for name in ("post-index-change", "reference-transaction", "post-merge"):
        (repo / ".git" / "hooks" / name).write_text(f'#!/bin/sh\necho ran >> "{marker}"\n')
        (repo / ".git" / "hooks" / name).chmod(0o755)
    return marker


CALLS = {
    "sorting a prompt": lambda repo: routing._git_tracked(
        repo / "a.txt", routing.Place(repo=repo, scratchpad=None, home=repo.parent, owner="o")
    ),
    "the session hook": lambda repo: session_hook._tracked(repo / "a.txt"),
    "the work cards": lambda repo: [
        work._git(repo, *args)
        for args in (("branch", "--show-current"), ("config", "branch.main.remote"), ("remote", "get-url", "origin"))
    ],
    "the live view's git status": lambda repo: gitstatus.cached(repo),
    "the software page": lambda repo: software._git(repo, "log", "-n2", "--format=%H", "--", "a.txt"),
    "repo sync": lambda repo: reposync._git(repo, "status", "--porcelain", "--untracked-files=all"),
    "the org chart's remotes": lambda repo: config._remotes(repo),
    "the updater": lambda repo: [
        updater._git(repo, *args)
        for args in (("status", "--porcelain"), ("fetch", "--quiet"), ("merge", "--ff-only", "@{upstream}"))
    ],
    # The push guard, restarts, shipped and metrics run these, in folders the test can't point them at.
    "every other subcommand the bridge runs": lambda repo: [
        safe_git.run("-C", str(repo), *args, capture_output=True, check=False)
        for args in (
            ("rev-parse", "--short", "HEAD"),
            ("rev-parse", "--abbrev-ref", "@{push}"),
            ("remote", "get-url", "--push", "origin"),
            ("rev-list", "--count", "HEAD"),
            ("symbolic-ref", "--short", "-q", "HEAD"),
            ("show", "HEAD:a.txt"),
            ("reset", "--hard", "HEAD"),
        )
    ],
}


@needs_sh
@pytest.mark.parametrize("how", ["set directly", "through include.path", "through includeIf"])
@pytest.mark.parametrize("call", sorted(CALLS))
def test_git_runs_nothing_in_a_repo_whose_own_settings_name_a_program(tmp_path, call, how):
    repo = _repo(tmp_path)
    marker = _plant(tmp_path, repo, how)
    subprocess.run(["git", "-C", str(repo), "status", "--porcelain"], capture_output=True, check=False)
    assert marker.exists(), "the positive control: plain git does start it"
    marker.unlink()
    CALLS[call](repo)
    assert not marker.exists(), f"{call} started a program the repository named"


@needs_sh
def test_the_bridge_runs_git_with_fsmonitor_and_hooks_off_and_no_system_config(tmp_path):
    """fsmonitor and hooks are switched off rather than refused, so a repo using them still works."""
    repo = _repo(tmp_path)
    marker, program = _marker_program(tmp_path)
    _git("-C", str(repo), "config", "core.fsmonitor", str(program))
    (repo / ".git" / "hooks" / "post-index-change").write_text(f'#!/bin/sh\necho ran >> "{marker}"\n')
    (repo / ".git" / "hooks" / "post-index-change").chmod(0o755)
    out = safe_git.run("-C", str(repo), "status", "--porcelain", capture_output=True, text=True, check=False)
    assert out.returncode == 0 and not marker.exists()
    words = safe_git.command("status")
    assert "core.fsmonitor=false" in words and f"core.hooksPath={os.devnull}" in words
    assert safe_git.env()["GIT_CONFIG_NOSYSTEM"] == "1"


@pytest.mark.parametrize(
    "key", ["merge.x.driver", "credential.https://x.helper", "something.new.textconv", "remote.origin.vcs"]
)
def test_a_setting_is_judged_by_how_it_ends_so_an_unlisted_one_fails_closed(tmp_path, key):
    repo = _repo(tmp_path)
    _git("-C", str(repo), "config", key, "x")
    assert safe_git.program_named(("-C", str(repo))) == key


def test_the_contributing_hooks_line_is_not_refused(tmp_path):
    repo = _repo(tmp_path)
    _git("-C", str(repo), "config", "core.hooksPath", ".githooks")
    assert safe_git.program_named(("-C", str(repo))) is None
    assert safe_git.run("-C", str(repo), "rev-parse", "HEAD", capture_output=True, check=False).returncode == 0


def test_a_skipped_git_says_which_repo_and_which_setting_and_fails_like_git(tmp_path, caplog):
    repo = _repo(tmp_path)
    _git("-C", str(repo), "config", "filter.x.process", "x")
    out = safe_git.run("-C", str(repo), "status", capture_output=True, text=True, check=False)
    why = f"git status skipped in {repo}: its own settings name a program (filter.x.process)"
    assert out.returncode == 1 and out.stdout == "" and out.stderr == why
    assert why in caplog.text
    with pytest.raises(subprocess.CalledProcessError):
        safe_git.run("-C", str(repo), "status", capture_output=True, check=True)


def test_the_live_view_shows_no_status_for_a_skipped_repo(tmp_path):
    repo = _repo(tmp_path)
    _git("-C", str(repo), "config", "filter.x.process", "x")
    gitstatus._cache.clear()
    assert gitstatus.cached(repo) is None


@needs_sh
def test_the_updater_reaches_a_remote_only_over_https(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    marker, program = _marker_program(tmp_path)
    _git("-C", str(repo), "remote", "set-url", "origin", "ssh://example.invalid/x.git")
    monkeypatch.setenv("GIT_SSH_COMMAND", str(program))
    ok, _ = updater._git(repo, "fetch", "--quiet")
    assert not ok and not marker.exists()


def _repo_with_a_submodule(tmp_path: Path) -> tuple[Path, Path]:
    """A repo whose submodule's own settings name the marker for its filter; the repo's don't."""
    inner = tmp_path / "inner"
    inner.mkdir()
    (inner / ".gitattributes").write_text("*.txt filter=f\n")
    (inner / "b.txt").write_text("b\n")
    _git("init", "-q", "-b", "main", str(inner))
    _git("-C", str(inner), "add", ".")
    _git("-C", str(inner), "commit", "-q", "-m", "one")
    repo = _repo(tmp_path)
    _git("-C", str(repo), "-c", "protocol.file.allow=always", "submodule", "add", "-q", str(inner), "sub")
    _git("-C", str(repo), "commit", "-q", "-m", "sub")
    (repo / "sub" / "b.txt").write_text("b\nc\n")  # so a reset by one commit checks the submodule out again
    _git("-C", str(repo / "sub"), "commit", "-q", "-am", "two")
    _git("-C", str(repo), "commit", "-q", "-am", "the submodule moves on")
    marker, program = _marker_program(tmp_path)
    settings = repo / ".git" / "modules" / "sub" / "config"
    settings.write_text(settings.read_text() + f'[filter "f"]\n\tclean = {program}\n\tsmudge = {program}\n')
    _git("-C", str(repo), "config", "submodule.recurse", "true")  # no ending marks this one
    stamp = (repo / "sub" / "b.txt").stat().st_mtime + 100
    os.utime(repo / "sub" / "b.txt", (stamp, stamp))
    return repo, marker


SUBMODULE_CALLS = {
    "the live view's git status": lambda repo: gitstatus.cached(repo),
    "repo sync": lambda repo: reposync._git(repo, "status", "--porcelain", "--untracked-files=all"),
    "the updater's status": lambda repo: updater._git(repo, "status", "--porcelain", "--untracked-files=no"),
    "the updater's reset": lambda repo: updater._git(repo, "reset", "--hard", "HEAD~1"),
}


@needs_sh
@pytest.mark.parametrize("call", sorted(SUBMODULE_CALLS))
def test_git_status_never_looks_inside_a_submodule(tmp_path, call):
    repo, marker = _repo_with_a_submodule(tmp_path)
    plain = ["reset", "--hard", "HEAD~1"] if "reset" in call else ["status", "--porcelain"]
    subprocess.run(["git", "-C", str(repo), *plain], capture_output=True, check=False)
    assert marker.exists(), "the positive control: plain git does start it"
    marker.unlink()
    _git("-C", str(repo), "reset", "-q", "--hard", "HEAD@{1}")  # back to where the positive control began
    marker.unlink(missing_ok=True)
    gitstatus._cache.clear()
    SUBMODULE_CALLS[call](repo)
    assert not marker.exists(), f"{call} stepped into a submodule and started its program"


@pytest.mark.parametrize("args", [("diff",), ("-C", "/x", "pull"), ("cat-file", "-p", "HEAD"), ()])
def test_a_git_subcommand_off_the_measured_list_is_refused(args):
    with pytest.raises(ValueError, match="not measured"):
        safe_git.command(*args)


@pytest.mark.parametrize("sub", sorted(safe_git.MEASURED))
def test_every_measured_subcommand_is_taken_past_any_leading_options(sub):
    words = safe_git.command("--no-optional-locks", "-c", "a.b=c", "-C", "/x", sub, git="/usr/bin/git")
    assert words[0] == "/usr/bin/git" and words[words.index("/x") + 1] == sub


@pytest.mark.skipif(sys.platform == "win32", reason="a named pipe is a POSIX file")
def test_a_repo_whose_settings_cannot_be_read_in_time_is_skipped(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    pipe = tmp_path / "pipe"
    os.mkfifo(pipe)  # nothing ever writes to it, so reading it waits for ever
    _git("-C", str(repo), "config", "include.path", str(pipe))
    monkeypatch.setattr(safe_git, "SETTINGS_SECONDS", 0.5)
    assert safe_git.program_named(("-C", str(repo))) == "its settings could not be read in time"
    out = safe_git.run("-C", str(repo), "rev-parse", "HEAD", capture_output=True, text=True, check=False, timeout=10)
    assert out.returncode == 1 and "skipped" in out.stderr


def _runs_git(call: ast.Call, git_names: set[str]) -> bool:
    """Whether this call starts a process whose first word is git."""
    if not call.args:
        return False
    first = call.args[0]
    if isinstance(first, ast.Constant) and isinstance(first.value, str):
        return first.value.split()[:1] == ["git"]
    if isinstance(first, (ast.List, ast.Tuple)) and first.elts:
        first = first.elts[0]
    if isinstance(first, ast.Starred):
        return False
    return (isinstance(first, ast.Constant) and first.value == "git") or (
        isinstance(first, ast.Name) and first.id in git_names
    )


SPAWNS = {"run", "Popen", "call", "check_call", "check_output", "create_subprocess_exec", "system", "execvp"}


def test_no_module_runs_git_but_through_safe_git():
    """A new git call that skips safe_git is caught here, not in review."""
    found = []
    for path in sorted(SRC.glob("*.py")):
        if path.name == "safe_git.py":
            continue
        tree = ast.parse(path.read_text())
        assigned = [
            (target.id, node.value)
            for node in ast.walk(tree)
            if isinstance(node, ast.Assign)
            for target in node.targets
            if isinstance(target, ast.Name)
        ]
        # A name holding git's path, from procs.find or shutil.which.
        git_names = {
            name
            for name, value in assigned
            if "git" in ast.unparse(value) and any(w in ast.unparse(value) for w in ("find(", "which("))
        }
        # A name holding a whole git command line, built before the call.
        git_names |= {
            name
            for name, value in assigned
            if isinstance(value, (ast.List, ast.Tuple))
            and _runs_git(ast.Call(func=value, args=[value], keywords=[]), git_names)
        }
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in SPAWNS:
                if _runs_git(node, git_names):
                    found.append(f"{path.name}:{node.lineno}")
    assert found == [], f"git run without safe_git at {found}"
