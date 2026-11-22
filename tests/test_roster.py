"""The sessions file: one row per role, kept so the sessions can be brought back."""

import os
import stat
import sys
import tomllib

import pytest

from claude_voice import roster

NOW = "2026-11-16T10:00+00:00"
LATER = "2026-11-16T10:05+00:00"


def live(name, sid, cwd="/work/shop", tmux="shop:@1.%1", kind="interactive", status="idle"):
    return {"name": name, "sessionId": sid, "cwd": cwd, "tmux": tmux, "kind": kind, "status": status}


def solo(d):
    return None


def test_a_running_session_is_written_as_a_row():
    rows = roster.seen([], [live("shop", "id-1")], solo, NOW)
    assert rows == [roster.Row("solo", "shop", "/work/shop", "id-1", "shop:@1.%1", NOW)]


def test_a_session_is_listed_under_its_programme():
    rows = roster.seen([], [live("shop", "id-1")], lambda d: "acme", NOW)
    assert rows[0].organisation == "acme"


def test_a_session_out_of_tmux_is_written_with_no_pane():
    rows = roster.seen([], [live("shop", "id-1", tmux=None)], solo, NOW)
    assert rows[0].tmux == ""


def test_a_background_or_unnamed_session_is_not_a_role():
    found = [live("helper", "id-1", kind="bg"), live("", "id-2"), live("shop", "")]
    assert roster.seen([], found, solo, NOW) == []


def test_a_role_that_has_gone_stays_in_the_sessions_file():
    before = roster.seen([], [live("shop", "id-1")], solo, NOW)
    assert roster.seen(before, [], solo, LATER) == before


def test_a_new_conversation_replaces_the_role_s_old_one():
    before = roster.seen([], [live("shop", "id-1")], solo, NOW)
    rows = roster.seen(before, [live("shop", "id-2")], solo, LATER)
    assert [(r.role, r.conversation, r.last_seen) for r in rows] == [("shop", "id-2", LATER)]


def test_a_conversation_is_listed_under_one_role_only():
    before = roster.seen([], [live("shop", "id-1")], solo, NOW)
    rows = roster.seen(before, [live("store", "id-1")], solo, LATER)
    assert [r.role for r in rows] == ["store"]


def test_the_rows_are_in_order_of_organisation_and_role():
    found = [live("b", "1"), live("a", "2"), live("c", "3", cwd="/work/acme")]
    rows = roster.seen([], found, lambda d: "acme" if d["cwd"] == "/work/acme" else None, NOW)
    assert [(r.organisation, r.role) for r in rows] == [("acme", "c"), ("solo", "a"), ("solo", "b")]


def test_the_file_is_read_back_as_it_was_written(tmp_path):
    path = tmp_path / "state" / "sessions.toml"
    rows = roster.seen([], [live("shop", "id-1"), live("store", "id-2", tmux=None)], solo, NOW)
    assert roster.write(path, rows) is True
    assert roster.read(path) == rows
    assert tomllib.loads(path.read_text())["session"][0]["role"] == "shop"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes")
def test_the_sessions_file_is_readable_by_its_owner_alone(tmp_path):
    path = tmp_path / "state" / "sessions.toml"
    roster.write(path, roster.seen([], [live("shop", "id-1")], solo, NOW))
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700


def test_an_unchanged_file_is_not_written_again(tmp_path):
    path = tmp_path / "sessions.toml"
    rows = roster.seen([], [live("shop", "id-1")], solo, NOW)
    roster.write(path, rows)
    os.utime(path, (0, 0))
    assert roster.write(path, rows) is False
    assert path.stat().st_mtime == 0


def test_a_missing_file_has_no_rows(tmp_path):
    assert roster.read(tmp_path / "none.toml") == []


def test_a_row_missing_a_field_is_skipped(tmp_path):
    path = tmp_path / "sessions.toml"
    whole = roster.dump(roster.seen([], [live("store", "id-2")], solo, NOW))
    path.write_text('[[session]]\nrole = "shop"\n\n' + whole)
    assert [r.role for r in roster.read(path)] == ["store"]


ARGV = ("/opt/claude", "--resume", "id-1", "--settings", "{}")


def test_a_row_keeps_the_start_command_its_session_runs_with(tmp_path):
    rows = roster.seen([], [live("shop", "id-1")], solo, NOW, argv=lambda d: ARGV)
    assert rows[0].argv == ARGV
    path = tmp_path / "sessions.toml"
    roster.write(path, rows)
    assert roster.read(path) == rows


def test_a_start_command_that_cannot_be_read_keeps_the_last_one_read():
    before = roster.seen([], [live("shop", "id-1")], solo, NOW, argv=lambda d: ARGV)
    assert roster.seen(before, [live("shop", "id-1")], solo, LATER, argv=lambda d: None)[0].argv == ARGV
    assert roster.seen(before, [live("shop", "id-2")], solo, LATER, argv=lambda d: None)[0].argv == ()


def test_a_row_written_before_start_commands_were_kept_is_still_read(tmp_path):
    path = tmp_path / "sessions.toml"
    path.write_text(
        '[[session]]\norganisation = "solo"\nrole = "shop"\nfolder = "/w"\nconversation = "id-1"\n'
        f'tmux = ""\nlast_seen = "{NOW}"\n',
        encoding="utf-8",
    )
    assert roster.read(path) == [roster.Row("solo", "shop", "/w", "id-1", "", NOW)]


def test_a_file_that_is_not_toml_says_what_to_do(tmp_path):
    path = tmp_path / "sessions.toml"
    path.write_text("not = [toml")
    try:
        roster.read(path)
    except roster.Unreadable as exc:
        assert str(path) in str(exc) and "remove it" in str(exc)
    else:
        raise AssertionError("an unreadable file was read")
