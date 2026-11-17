"""The owner's typed accept of a repository's rights files (designs: rolling restart v2, §1)."""

from __future__ import annotations

import fnmatch
import json
import re
import stat
import sys
from pathlib import Path

import pytest

from claude_voice import accepted, cli


def settings(repo: Path, body: dict, name: str = "settings.local.json") -> Path:
    file = repo / ".claude" / name
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
    return file


@pytest.fixture
def state(tmp_path, monkeypatch):
    """The record and the private copies live in a state folder of the test's own."""
    folder = tmp_path / "state"
    monkeypatch.setattr(accepted, "RECORD", folder / "accepted.json")
    monkeypatch.setattr(accepted, "COPIES", folder / "accepted")
    return folder


def accept_all(repo: Path) -> None:
    accepted.accept(
        accepted.show(repo, accepted.RECORD, accepted.COPIES), record=accepted.RECORD, copies=accepted.COPIES
    )


def kinds(repo: Path) -> list[tuple[str, str]]:
    shown = accepted.show(repo, accepted.RECORD, accepted.COPIES)
    return [(line.kind, line.text) for item in shown for line in item.lines]


# -- what is shown ----------------------------------------------------------------------


def test_a_file_never_accepted_before_is_shown_as_all_widening(tmp_path, state):
    settings(tmp_path, {"permissions": {"deny": ["Bash(rm -rf /)"]}})
    lines = kinds(tmp_path)
    assert lines and {kind for kind, _ in lines} == {accepted.WIDENS}


def test_the_lines_that_widen_a_sessions_rights_are_shown_first(tmp_path, state):
    settings(tmp_path, {"permissions": {"allow": ["Bash(ls*)"], "deny": ["Bash(git reset --hard*)"]}})
    accept_all(tmp_path)
    settings(tmp_path, {"permissions": {"allow": ["Bash(ls*)", "Bash(curl *)"], "deny": ["Bash(rm -rf /)"]}})
    lines = kinds(tmp_path)
    assert lines == [
        (accepted.WIDENS, "+ allow Bash(curl *)"),
        (accepted.WIDENS, "- deny Bash(git reset --hard*)"),
        (accepted.NARROWS, "+ deny Bash(rm -rf /)"),
    ]


def test_a_new_hook_or_any_other_setting_widens(tmp_path, state):
    settings(tmp_path, {"permissions": {"allow": []}})
    accept_all(tmp_path)
    settings(tmp_path, {"permissions": {"allow": [], "defaultMode": "bypassPermissions"}, "hooks": {"Stop": []}})
    assert {kind for kind, _ in kinds(tmp_path)} == {accepted.WIDENS}
    assert [text for _, text in kinds(tmp_path)] == [
        '~ defaultMode: null -> "bypassPermissions"',
        '~ hooks: null -> {"Stop": []}',
    ]


def test_a_file_that_matches_its_accepted_version_is_not_shown(tmp_path, state):
    settings(tmp_path, {"permissions": {"allow": ["Bash(ls*)"]}})
    accept_all(tmp_path)
    assert accepted.show(tmp_path, accepted.RECORD, accepted.COPIES) == []


def test_the_diff_is_against_the_private_copy_not_against_git(tmp_path, state):
    file = settings(tmp_path, {"permissions": {"allow": ["Bash(ls*)"]}})
    accept_all(tmp_path)
    copy = state / "accepted" / accepted.sha256(file.read_bytes())
    assert copy.read_bytes() == file.read_bytes()
    if sys.platform != "win32":
        assert stat.S_IMODE(copy.stat().st_mode) == 0o600


# -- what is recorded -------------------------------------------------------------------


def test_an_accept_records_only_the_files_it_showed(tmp_path, state):
    settings(tmp_path, {"permissions": {"allow": ["Bash(ls*)"]}})
    shown = accepted.show(tmp_path, accepted.RECORD, accepted.COPIES)
    shared = settings(tmp_path, {"permissions": {"allow": ["Bash(curl *)"]}}, name="settings.json")
    accepted.accept(shown, record=accepted.RECORD, copies=accepted.COPIES)
    record = accepted.read_record(accepted.RECORD)
    assert str(shared.resolve()) not in record
    assert [Path(p).name for p in record] == ["settings.local.json"]


def test_a_file_changed_after_it_was_shown_is_not_accepted(tmp_path, state):
    file = settings(tmp_path, {"permissions": {"allow": ["Bash(ls*)"]}})
    shown = accepted.show(tmp_path, accepted.RECORD, accepted.COPIES)
    settings(tmp_path, {"permissions": {"allow": ["Bash(ls*)", "Bash(curl *)"]}})
    with pytest.raises(accepted.Changed, match=re.escape(str(file.resolve()))):
        accepted.accept(shown, record=accepted.RECORD, copies=accepted.COPIES)
    assert accepted.read_record(accepted.RECORD) == {}


def test_the_record_says_where_the_accept_came_from(tmp_path, state):
    settings(tmp_path, {"permissions": {}})
    accept_all(tmp_path)
    (entry,) = accepted.read_record(accepted.RECORD).values()
    assert entry["source"] == "typed" and len(entry["sha256"]) == 64


# -- the typed command ------------------------------------------------------------------


needs_a_terminal = pytest.mark.skipif(
    sys.platform == "win32", reason="the accept is typed at a POSIX terminal; Windows runs it in WSL"
)


@pytest.fixture
def at_a_terminal(monkeypatch):
    monkeypatch.setattr(cli, "_terminal", lambda env: True)


def typed(monkeypatch, answer: str) -> list[str]:
    asked: list[str] = []

    def read(prompt: str) -> str:
        asked.append(prompt)
        return answer

    monkeypatch.setattr(cli, "_read_from_terminal", read)
    return asked


@needs_a_terminal
def test_the_owner_accepts_a_repositorys_rights_by_typing_its_name(tmp_path, state, at_a_terminal, monkeypatch, capsys):
    repo = tmp_path / "voice-repo"
    settings(repo, {"permissions": {"allow": ["Bash(curl *)"]}})
    typed(monkeypatch, "yes")
    assert cli.main(["permissions", "--accept", str(repo)], env={}) == 1
    assert accepted.read_record(accepted.RECORD) == {}
    asked = typed(monkeypatch, "voice-repo")
    assert cli.main(["permissions", "--accept", str(repo)], env={}) == 0
    out = capsys.readouterr().out
    assert out.index("WIDENS") < out.index("Accepted")
    assert asked and "voice-repo" in asked[0]
    assert accepted.show(repo, accepted.RECORD, accepted.COPIES) == []


@needs_a_terminal
def test_anything_but_the_repositorys_name_accepts_nothing(tmp_path, state, at_a_terminal, monkeypatch, capsys):
    repo = tmp_path / "voice-repo"
    settings(repo, {"permissions": {"allow": ["Bash(curl *)"]}})
    typed(monkeypatch, "yes")
    assert cli.main(["permissions", "--accept", str(repo)], env={}) == 1
    assert "Not accepted" in capsys.readouterr().out
    assert accepted.read_record(accepted.RECORD) == {}


@needs_a_terminal
def test_an_accept_is_typed_in_a_terminal_never_piped(tmp_path, state, monkeypatch, capsys):
    repo = tmp_path / "voice-repo"
    settings(repo, {"permissions": {"allow": ["Bash(curl *)"]}})
    monkeypatch.setattr(cli, "_terminal", lambda env: False)
    asked = typed(monkeypatch, "voice-repo")
    assert cli.main(["permissions", "--accept", str(repo)], env={}) == 1
    assert not asked and accepted.read_record(accepted.RECORD) == {}
    assert "terminal" in capsys.readouterr().err


@needs_a_terminal
def test_an_accept_names_one_repository(tmp_path, state, at_a_terminal, capsys):
    assert cli.main(["permissions", "--accept", str(tmp_path / "a"), str(tmp_path / "b")], env={}) == 1
    assert cli.main(["permissions", "--accept"], env={}) == 1
    assert "one repository" in capsys.readouterr().err


@needs_a_terminal
def test_a_repository_with_nothing_new_says_so(tmp_path, state, at_a_terminal, monkeypatch, capsys):
    repo = tmp_path / "voice-repo"
    repo.mkdir()
    asked = typed(monkeypatch, "voice-repo")
    assert cli.main(["permissions", "--accept", str(repo)], env={}) == 0
    assert not asked and "nothing to accept" in capsys.readouterr().out


@needs_a_terminal
def test_a_file_changed_while_the_owner_read_it_starts_over(tmp_path, state, at_a_terminal, monkeypatch, capsys):
    repo = tmp_path / "voice-repo"
    file = settings(repo, {"permissions": {"allow": ["Bash(ls*)"]}})

    def read(prompt: str) -> str:
        file.write_text('{"permissions": {"allow": ["Bash(*)"]}}\n', encoding="utf-8")
        return "voice-repo"

    monkeypatch.setattr(cli, "_read_from_terminal", read)
    assert cli.main(["permissions", "--accept", str(repo)], env={}) == 1
    assert "changed after it was shown" in capsys.readouterr().err
    assert accepted.read_record(accepted.RECORD) == {}


def test_no_session_can_accept_rights_for_the_owner():
    from claude_voice import permissions

    deny = permissions.load().deny
    for command in (
        "claude-voice permissions --accept .",
        "uv run claude-voice permissions --accept ~/src/x",
        "uv run --directory ~/b claude-voice permissions --dry-run --accept .",
    ):
        assert any(fnmatch.fnmatchcase(command, rule[5:-1]) for rule in deny if rule.startswith("Bash(")), command


def test_on_windows_the_accept_says_to_run_it_in_wsl(tmp_path, state, monkeypatch, capsys):
    monkeypatch.setattr(cli.sys, "platform", "win32")
    assert cli.main(["permissions", "--accept", str(tmp_path)], env={}) == 1
    assert "WSL" in capsys.readouterr().err


def test_a_file_that_is_not_utf8_text_is_refused_and_never_copied_wrongly(tmp_path, state):
    file = tmp_path / ".claude" / "settings.local.json"
    file.parent.mkdir(parents=True)
    file.write_bytes(b'{"permissions": {"allow": ["Bash(\xff)"]}}\n')
    with pytest.raises(accepted.Changed, match="not UTF-8"):
        accept_all(tmp_path)
    assert accepted.read_record(accepted.RECORD) == {}


def test_the_accepted_copy_is_the_shown_bytes_exactly(tmp_path, state, monkeypatch):
    from claude_voice import safefile

    monkeypatch.setattr(safefile.os, "linesep", "\r\n")  # as Windows would turn each \n
    file = tmp_path / ".claude" / "settings.local.json"
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_bytes(b'{"permissions": {"allow": ["Bash(ls*)"]}}\r\n')
    accept_all(tmp_path)
    assert (state / "accepted" / accepted.sha256(file.read_bytes())).read_bytes() == file.read_bytes()
    safefile.replace(tmp_path / "plain.txt", "a\nb\n")
    assert (tmp_path / "plain.txt").read_bytes() == b"a\r\nb\r\n"  # text still gets the system's endings
