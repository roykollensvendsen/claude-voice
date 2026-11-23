"""The session hook: what a session may do without asking the owner, and nothing more.

It lets a recursive remove through only inside the session's own scratchpad, and an
issue only in a repository of the session's own programme. Everything else it asks
about, or leaves to Claude Code's own rules."""

from __future__ import annotations

import ast
import functools
import json
import subprocess
import sys
from pathlib import Path

import pytest

from claude_voice import session_hook

SID = "0f3c2a1b-1111-2222-3333-444455556666"
OTHER = "9e8d7c6b-1111-2222-3333-444455556666"
OWN = ("acme/bridge", "acme/garage")

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="Claude Code's scratchpad is a POSIX path")


@pytest.fixture
def tmp_root(tmp_path: Path) -> Path:
    root = tmp_path / "claude-1000"
    for sid in (SID, OTHER):
        (root / "-home-x-repo" / sid / "scratchpad" / "build" / "deep").mkdir(parents=True)
    return root


@pytest.fixture
def terms(tmp_path: Path) -> Path:
    file = tmp_path / "terms"
    file.write_text("zorblax\nquimbly\n", encoding="utf-8")
    return file


def pad(root: Path, sid: str = SID) -> Path:
    return root / "-home-x-repo" / sid / "scratchpad"


def decide(command: str, root: Path, terms: Path | None = None, cwd: Path | None = None) -> str | None:
    hook_input = {
        "tool_name": "Bash",
        "session_id": SID,
        "cwd": str(cwd or root),
        "tool_input": {"command": command},
    }
    return session_hook.decision(hook_input, tmp_root=root, repos=OWN, terms=terms)


# -- recursive removes -------------------------------------------------------------------


@posix_only
def test_a_recursive_remove_is_let_through_only_inside_the_sessions_own_scratchpad(tmp_root):
    assert decide(f"rm -rf {pad(tmp_root)}/build", tmp_root) == "allow"
    assert decide(f"rm -r -f -- {pad(tmp_root)}/build/deep", tmp_root) == "allow"
    assert decide("rm -r build", tmp_root, cwd=pad(tmp_root)) == "allow"
    assert decide(f"rm -r {pad(tmp_root, OTHER)}/build", tmp_root) == "ask"
    assert decide(f"rm -r {pad(tmp_root)}", tmp_root) == "ask"  # the scratchpad itself
    assert decide("rm -r /home/x/repo/build", tmp_root) == "ask"
    assert decide(f"rm -rf {pad(tmp_root)}/*", tmp_root) == "ask"  # what a glob names is unknown
    assert decide(f"rm -rf {pad(tmp_root)}/build; ls", tmp_root) == "ask"  # nor what follows


@posix_only
def test_a_way_out_of_the_scratchpad_is_asked_about(tmp_root, tmp_path):
    assert decide(f"rm -r {pad(tmp_root)}/build/../../../{OTHER}/scratchpad/build", tmp_root) == "ask"
    outside = tmp_path / "precious"
    outside.mkdir()
    (pad(tmp_root) / "link").symlink_to(outside)
    assert decide(f"rm -r {pad(tmp_root)}/link", tmp_root) == "ask"
    assert decide(f"rm -r {pad(tmp_root)}/link/inner", tmp_root) == "ask"


@posix_only
@pytest.mark.parametrize(
    "tail",
    [
        "/build && rm -r /home/x",
        "/build; ls",
        "/build | cat",
        "/* ",
        "/b?ild",
        "/{build,deep}",
        "/$(echo build)",
        "/build > /dev/null",
    ],
)
def test_a_chained_or_globbed_remove_is_asked_about(tmp_root, tail):
    assert decide(f"rm -rf {pad(tmp_root)}{tail}", tmp_root) == "ask"


@posix_only
def test_any_other_recursive_remove_is_asked_about(tmp_root):
    assert decide("cd /home/x && rm -rf repo", tmp_root) == "ask"
    assert decide("find . | xargs rm -r", tmp_root) == "ask"
    assert decide("rm --recursive /home/x/repo", tmp_root) == "ask"
    assert decide('rm -r "unclosed', tmp_root) == "ask"
    assert decide(f"rm -ri {pad(tmp_root)}/build", tmp_root) == "ask"  # an option it does not know


def test_other_commands_are_left_to_claude_codes_own_rules(tmp_root):
    for command in ("ls -la", "git status", "docker run --rm alpine true", "uv run pytest -q"):
        assert decide(command, tmp_root) is None, command


@posix_only
@pytest.mark.parametrize(
    "command",
    [
        '"rm" -r /home/x/src',
        "\\rm -r /home/x/src",
        "r''m -r /home/x/src",
        "A=git rm -r /home/x/src",
        "command rm -r /home/x/src",
        "env rm -rf /home/x/src",
        "/bin/rm -r /home/x/src",
        "find /home/x/src -delete",
        "find . -exec rm {} +",
        "rm --recu /home/x/src",
        "rm --r /home/x/src",
        "python3 -c 'import shutil; shutil.rmtree(\"/home/x\")'",
    ],
)
def test_any_recursive_remove_asks_however_it_is_spelled(tmp_root, command):
    assert decide(command, tmp_root) == "ask"


@pytest.mark.parametrize(
    "command",
    ['"rm" notes.txt', "rm '-r' build", 'rm "-rf" build', "rm $FLAGS build", "rm -$X build", "/bin/r\\m build"],
)
def test_a_single_file_rm_is_exempt_only_with_a_plain_command_word_and_plain_flags(tmp_root, command):
    assert decide(command, tmp_root) == "ask"


def test_a_single_file_remove_and_git_rm_keep_their_old_result(tmp_root):
    for command in ("rm notes.txt", "git rm notes.txt", "git rm -r --cached build", 'rm "a file.txt"'):
        assert decide(command, tmp_root) is None, command


@posix_only
def test_a_quoted_remove_inside_the_scratchpad_still_asks(tmp_root):
    assert decide(f'rm -r "{pad(tmp_root)}/build"', tmp_root) == "ask"


# -- issues --------------------------------------------------------------------------------


def body(root: Path, text: str) -> Path:
    """An issue body in the session's own scratchpad, the one place a body file may come from
    besides the files git tracks."""
    file = pad(root) / "issue.md"
    file.write_text(text, encoding="utf-8")
    return file


@posix_only  # an allowed issue needs grep, which Windows lacks; there it asks
def test_an_issue_is_filed_without_asking_only_in_the_programmes_own_repositories(tmp_root, terms, tmp_path):
    file = body(tmp_root, "The garage door stops halfway.\n")
    ok = f'gh issue create --repo acme/garage --title "Door stops" --body-file {file}'
    assert decide(ok, tmp_root, terms) == "allow"
    assert decide('gh issue create -R Acme/Garage -t "Door stops" -b "It stops."', tmp_root, terms) == "allow"
    assert decide(ok.replace("acme/garage", "someone/else"), tmp_root, terms) == "ask"
    assert decide(f'gh issue create --title "Door stops" --body-file {file}', tmp_root, terms) == "ask"
    assert decide(ok + " && gh pr merge 1", tmp_root, terms) == "ask"
    assert decide('gh issue create --repo acme/garage --title "Door stops" --web', tmp_root, terms) == "ask"


def test_an_issue_that_names_a_personal_term_is_asked_about(tmp_root, terms, tmp_path):
    file = body(tmp_root, "Seen on Zorblax's laptop.\n")
    assert decide(f'gh issue create --repo acme/garage -t "Door" -F {file}', tmp_root, terms) == "ask"
    assert decide('gh issue create --repo acme/garage -t "quimbly door" -b "x"', tmp_root, terms) == "ask"
    assert decide('gh issue create --repo acme/garage -t "Door" -b "x"', tmp_root, None) == "ask"
    assert decide('gh issue create --repo acme/garage -t "Door" -b "x"', tmp_root, tmp_path / "gone") == "ask"


@posix_only  # an allowed issue needs grep, which Windows lacks; there it asks
@pytest.mark.parametrize("taken", ["--assignee @me", "--assignee=@me", "-a @me"])
def test_a_session_may_take_the_issue_it_files(tmp_root, terms, taken):
    assert decide(f'gh issue create -R acme/garage -t "Door" -b "x" {taken}', tmp_root, terms) == "allow"


@pytest.mark.parametrize(
    "given",
    ["--assignee bob", "-a bob", "--assignee @me,bob", "--assignee @me --assignee bob", "-a bob -a @me"],
)
def test_an_issue_assigned_to_anyone_but_the_session_itself_is_asked_about(tmp_root, terms, given):
    assert decide(f'gh issue create -R acme/garage -t "Door" -b "x" {given}', tmp_root, terms) == "ask"


def test_an_issue_that_gives_a_flag_twice_is_asked_about(tmp_root, terms):
    """Only the last of a repeated flag would be checked, while gh uses every one."""
    two = 'gh issue create -R acme/garage -t "Door" -b "x" -l quimbly -l bug'  # the last one alone is fine
    assert decide(two, tmp_root, terms) == "ask"


# -- the hook as Claude Code runs it ------------------------------------------------------


@posix_only
def test_the_hook_answers_claude_code_in_its_own_words(tmp_root, terms):
    script = Path(session_hook.__file__)
    hook_input = {"tool_name": "Bash", "session_id": SID, "cwd": "/", "tool_input": {"command": "rm -r /home/x"}}
    out = subprocess.run(
        [
            sys.executable,
            "-I",
            str(script),
            "--repos",
            ",".join(OWN),
            "--terms",
            str(terms),
            "--tmp-root",
            str(tmp_root),
        ],
        input=json.dumps(hook_input),
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    said = json.loads(out.stdout)["hookSpecificOutput"]
    assert said["hookEventName"] == "PreToolUse" and said["permissionDecision"] == "ask"


def test_a_hook_that_cannot_decide_asks(tmp_root, monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(session_hook, "_removal", broken)
    hook_input = {"tool_name": "Bash", "session_id": SID, "cwd": "/", "tool_input": {"command": "rm -r /x"}}
    assert (
        session_hook.answer(hook_input, tmp_root=tmp_root, repos=OWN, terms=None)["hookSpecificOutput"][
            "permissionDecision"
        ]
        == "ask"
    )


@posix_only
def test_the_session_hook_runs_with_the_standard_library_only(tmp_path):
    """Claude Code starts the hook with the system's python3, where the bridge's package isn't
    installed. -S leaves out every installed package, as that python3 has none of ours."""
    here = functools.partial(subprocess.run, cwd=tmp_path)
    said = session_hook.self_check([sys.executable, "-I", "-S", session_hook.__file__], run=here)
    assert said["ok"], said


def test_the_session_hook_and_the_files_beside_it_import_only_the_standard_library():
    """Read from the source, so a new import can't stop the hook again while every test still
    runs it with the bridge's own Python. A file beside it must keep to the same rule."""
    seen, outside = imports_beyond_the_standard_library(Path(session_hook.__file__).parent, "session_hook")
    assert seen >= {"session_hook", "safe_git"}
    assert not outside, f"imports beyond the standard library: {outside}"


def test_a_file_imported_beside_the_hook_by_its_own_name_is_read_too(tmp_path):
    (tmp_path / "hook.py").write_text("from .helper import go\n", encoding="utf-8")
    (tmp_path / "helper.py").write_text("import requests\n\ndef go(): ...\n", encoding="utf-8")
    seen, outside = imports_beyond_the_standard_library(tmp_path, "hook")
    assert seen == {"hook", "helper"}
    assert outside == ["helper: requests"]


def imports_beyond_the_standard_library(folder, start):
    """Each file the hook loads beside it, from `start`, and every import of theirs that is
    not in the standard library."""
    seen, todo, outside = set(), [start], []
    while todo:
        name = todo.pop()
        if name in seen:
            continue
        seen.add(name)
        tree = ast.parse((folder / f"{name}.py").read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.level:
                # from .x import y loads x.py; from . import x loads x.py
                todo += [node.module] if node.module else [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                outside += [f"{name}: {node.module}"] if node.module.split(".")[0] not in STDLIB else []
            elif isinstance(node, ast.Import):
                outside += [f"{name}: {a.name}" for a in node.names if a.name.split(".")[0] not in STDLIB]
            elif isinstance(node, ast.Call) and getattr(node.func, "id", None) == "_beside":
                todo.append(node.args[0].value)  # loaded by its path: _beside("safe_git")
    return seen, outside


STDLIB = sys.stdlib_module_names | {"__future__"}


@posix_only
def test_a_hook_that_does_not_answer_is_shown_as_an_error_with_the_fix(tmp_path):
    good = session_hook.self_check([sys.executable, "-I", session_hook.__file__])
    assert good["ok"], good
    bad = session_hook.self_check([sys.executable, "-I", str(tmp_path / "missing.py")])
    assert not bad["ok"] and "permissions" in bad["fix"]


# -- written beside the rules -------------------------------------------------------------


def test_the_session_hook_is_written_once_beside_the_rules_and_every_other_hook_is_kept(tmp_path):
    from claude_voice import permissions

    repo = tmp_path / "repo"
    (repo / ".claude").mkdir(parents=True)
    own = {"matcher": "Bash", "hooks": [{"type": "command", "command": "my-own-hook"}]}
    (repo / permissions.SETTINGS).write_text(json.dumps({"hooks": {"PreToolUse": [own]}}), encoding="utf-8")
    rules = permissions.Rules(allow=[], deny=[])
    first = permissions.hook_command(["acme/garage"], None)
    out = permissions.apply(repo, rules, backup_root=tmp_path / "b", hook=first)
    assert "session hook" in out.line()
    second = permissions.hook_command(["acme/garage", "acme/tv"], tmp_path / "terms")
    permissions.apply(repo, rules, backup_root=tmp_path / "b", hook=second)
    written = json.loads((repo / permissions.SETTINGS).read_text())["hooks"]["PreToolUse"]
    commands = [h["command"] for entry in written for h in entry["hooks"]]
    assert commands == ["my-own-hook", second]
    again = permissions.apply(repo, rules, backup_root=tmp_path / "b", hook=second)
    assert again.line().endswith("already has every rule")


def test_a_programmes_repositories_are_only_those_its_chart_lists():
    from claude_voice import org

    garage = org.Component("garage", "Garage", remotes=("Acme/Garage",))
    tv = org.Component("tv", "TV", folders=(Path("/x/tv"),))
    assert org.repositories(org.Programme("cv", "CV", components=(garage, tv))) == ["acme/garage"]
    assert org.repositories(None) == []


def test_the_terms_file_comes_from_the_bridges_own_settings(tmp_path):
    from claude_voice import config

    file = tmp_path / "config.toml"
    file.write_text('personal_terms_file = "~/private/terms"\n', encoding="utf-8")
    cfg = config.load(file, env={"HOME": str(tmp_path)})
    assert cfg.personal_terms_file == tmp_path / "private" / "terms"


@posix_only
def test_permissions_check_runs_each_repositorys_hook_once(tmp_path, capsys):
    from claude_voice import cli, permissions

    good, bare = tmp_path / "good", tmp_path / "bare"
    for repo in (good, bare):
        repo.mkdir()
    permissions.apply(
        good,
        permissions.Rules(allow=[], deny=[]),
        backup_root=tmp_path / "b",
        hook=permissions.hook_command([], None, python=sys.executable),
    )
    assert cli.main(["permissions", "--check", str(good)], env={}) == 0
    assert "answers" in capsys.readouterr().out
    assert cli.main(["permissions", "--check", str(bare)], env={}) == 1
    assert "claude-voice permissions" in capsys.readouterr().out


def test_health_shows_a_session_hook_that_cannot_start(monkeypatch):
    from claude_voice import doctor

    class NoPython:
        def which(self, name):
            return None

    found = doctor._session_hook(NoPython())
    assert not found["ok"] and found["fix"]


# -- an issue never carries a secret ------------------------------------------------------


@posix_only  # an allowed issue needs grep, which Windows lacks; there it asks
def test_an_issue_whose_body_file_is_neither_tracked_nor_in_the_scratchpad_is_asked_about(tmp_root, terms, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / ".gitignore").write_text("wifi.txt\n", encoding="utf-8")
    (repo / "notes.md").write_text("The door stops.\n", encoding="utf-8")
    (repo / "wifi.txt").write_text("the door's network\n", encoding="utf-8")
    (repo / "loose.md").write_text("Not added.\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "notes.md", ".gitignore"], check=True)
    env = tmp_path / ".config" / "claude-voice" / "env"
    env.parent.mkdir(parents=True)
    env.write_text("CLAUDE_VOICE_TOKEN=abc\n", encoding="utf-8")
    base = 'gh issue create -R acme/garage -t "Door" -F '
    assert decide(base + "notes.md", tmp_root, terms, cwd=repo) == "allow"
    assert decide(base + "wifi.txt", tmp_root, terms, cwd=repo) == "ask"  # ignored
    assert decide(base + "loose.md", tmp_root, terms, cwd=repo) == "ask"  # untracked
    assert decide(base + str(env), tmp_root, terms, cwd=repo) == "ask"
    assert decide(base + ".git/config", tmp_root, terms, cwd=repo) == "ask"
    assert decide(base + str(pad(tmp_root, OTHER) / "x.md"), tmp_root, terms, cwd=repo) == "ask"


def test_an_issue_that_names_a_variable_or_a_home_folder_is_asked_about(tmp_root, terms):
    assert decide('gh issue create -R acme/garage -t "Door" -b "$GH_TOKEN"', tmp_root, terms) == "ask"
    assert decide('gh issue create -R acme/garage -t "Door" -b "${X}"', tmp_root, terms) == "ask"
    assert decide("gh issue create -R acme/garage -t Door -F ~/.ssh/id_ed25519", tmp_root, terms) == "ask"


@pytest.mark.parametrize(
    "text",
    [
        "ghp_" + "a1B2" * 9,
        "github_pat_" + "a1B2" * 8,
        "sk-" + "a1B2c3" * 4,
        "xoxb-1234567890-abcdefghij",
        "AKIA" + "ABCDEFGHIJ234567",
        "-----BEGIN OPENSSH PRIVATE KEY-----",
        "f" * 12 + "0123456789abcdef" * 2,
        "dGhpcyBpcyBhIHNlY3JldCB0aGF0IGxvb2tzIGxpa2UgYmFzZTY0IQ",
        "api_key = s3cr3tvalue",
    ],
)
def test_an_issue_that_holds_a_secret_shape_is_asked_about(tmp_root, terms, tmp_path, text):
    assert decide(f'gh issue create -R acme/garage -t "Door" -b "{text}"', tmp_root, terms) == "ask"
    file = body(tmp_root, f"Seen this:\n{text}\n")
    assert decide(f'gh issue create -R acme/garage -t "Door" -F {file}', tmp_root, terms) == "ask"


@posix_only  # an allowed issue needs grep, which Windows lacks; there it asks
def test_a_commit_named_in_an_issue_is_not_taken_for_a_secret(tmp_root, terms):
    sha = "0123456789abcdef0123456789abcdef01234567"
    assert decide(f'gh issue create -R acme/garage -t "Door" -b "Since {sha}."', tmp_root, terms) == "allow"


def test_the_hooks_secret_files_are_the_rules_secret_files():
    from claude_voice import permissions

    raw = json.loads(Path(permissions.__file__).with_name("permissions.json").read_text(encoding="utf-8"))
    assert list(session_hook.SECRET_FILES) == raw["secrets"]["files"]


def test_every_abbreviation_of_recursive_counts_as_recursive(tmp_root):
    for flag in ("--r", "--re", "--recursive"):
        assert decide(f"rm {flag} /home/x/src", tmp_root) == "ask", flag
    assert decide("rm --force /home/x/notes.txt", tmp_root) is None
