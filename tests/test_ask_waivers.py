"""The owner keeps a named ask out of a named folder (ask-waivers v1.1).

`claude-voice permissions` adds every shipped ask to each folder. The owner's waiver file,
written by hand and by no command, keeps an exact, waivable ask text out of one folder for
good. A deny is never waived and an allow never touched. A file that cannot be trusted
waives nothing, so every ask comes back, and the run's first line says why. The folders here
are made up.
"""

from __future__ import annotations

import json
import os
import sys
import threading

import pytest

from claude_voice import permissions, waivers
from claude_voice.cli import main

REVIEW = "Bash(gh pr review*)"
WHY = "its gate sorts the safe forms; approve stays denied"
POSIX = pytest.mark.skipif(sys.platform == "win32", reason="links and pipes are POSIX here")


@pytest.fixture(scope="module")
def rules():
    return permissions.load()


def waiver_file(tmp_path, body):
    file = tmp_path / "ask-waivers.toml"
    file.write_text(body, encoding="utf-8")
    return file


def entry(folder, asks=(REVIEW,), why=WHY):
    return f"[[folder]]\npath = {json.dumps(str(folder))}\nasks = {json.dumps(list(asks))}\nwhy = {json.dumps(why)}\n"


def clone(tmp_path, name="some-clone"):
    folder = tmp_path / name
    folder.mkdir()
    return folder


def settings(folder):
    return json.loads((folder / permissions.SETTINGS).read_text(encoding="utf-8"))


def run(folder, rules, found, tmp_path, **kwargs):
    return permissions.apply(folder, rules, backup_root=tmp_path / "backups", waiver=found.for_folder(folder), **kwargs)


def refusal(rules, tmp_path, text):
    """Why a file naming `text` beside the review ask waives nothing, said first; empty when it waives."""
    folder = clone(tmp_path)
    found = waivers.read(rules, waiver_file(tmp_path, entry(folder, asks=(REVIEW, text))))
    if found.for_folder(folder) is not None:
        return ""
    first = found.lines()[0]
    assert text in first and "every ask" in first
    return first


# -- the table ---------------------------------------------------------------------------


def test_the_table_lets_a_folder_waive_only_the_review_ask(rules):
    assert rules.waivable == [REVIEW]
    assert all(text in rules.ask for text in rules.waivable)


def test_only_an_ask_the_table_ships_can_be_marked_waivable(tmp_path):
    table = json.loads(permissions.TABLE.read_text(encoding="utf-8"))
    table["waivable asks"] = ["Bash(gh pr merge*)"]
    broken = tmp_path / "permissions.json"
    broken.write_text(json.dumps(table), encoding="utf-8")
    with pytest.raises(permissions.PermissionsError, match=r"gh pr merge"):
        permissions.load(broken)


# -- what a waiver does ------------------------------------------------------------------


def test_a_waived_ask_is_not_added_to_its_folder(tmp_path, rules):
    folder = clone(tmp_path)
    found = waivers.read(rules, waiver_file(tmp_path, entry(folder)))
    run(folder, rules, found, tmp_path)
    assert REVIEW not in settings(folder)["permissions"]["ask"]
    run(folder, rules, found, tmp_path)
    assert REVIEW not in settings(folder)["permissions"]["ask"]
    assert "Bash(gh pr review* --approve*)" in settings(folder)["permissions"]["deny"]


def test_a_waived_ask_the_owner_kept_is_left_alone(tmp_path, rules):
    folder = clone(tmp_path)
    (folder / ".claude").mkdir()
    (folder / permissions.SETTINGS).write_text(json.dumps({"permissions": {"ask": [REVIEW]}}), encoding="utf-8")
    found = waivers.read(rules, waiver_file(tmp_path, entry(folder)))
    run(folder, rules, found, tmp_path)
    assert settings(folder)["permissions"]["ask"].count(REVIEW) == 1


def test_a_folder_without_a_waiver_gets_every_ask(tmp_path, rules):
    folder, other = clone(tmp_path), clone(tmp_path, "other-clone")
    found = waivers.read(rules, waiver_file(tmp_path, entry(other)))
    run(folder, rules, found, tmp_path)
    assert REVIEW in settings(folder)["permissions"]["ask"]


@POSIX
def test_a_waiver_matches_its_folder_by_the_real_path(tmp_path, rules):
    folder = clone(tmp_path)
    link = tmp_path / "linked"
    link.symlink_to(folder, target_is_directory=True)
    found = waivers.read(rules, waiver_file(tmp_path, entry(link)))
    assert found.for_folder(folder) is not None
    assert found.for_folder(link) is not None


@pytest.mark.parametrize(
    "command",
    [
        "gh pr review 12 --approve",
        "gh pr review --approve 12",
        "gh pr review 12 -a",
        "gh pr review -R o/r 12 --approve=true",
    ],
)
def test_approving_stays_denied_with_the_review_ask_waived(tmp_path, rules, command):
    folder = clone(tmp_path)
    found = waivers.read(rules, waiver_file(tmp_path, entry(folder)))
    run(folder, rules, found, tmp_path)
    written = settings(folder)["permissions"]
    here = permissions.Rules(allow=written["allow"], deny=written["deny"], ask=written["ask"])
    assert permissions.decision(here, command) == "refused", command


# -- what can be waived ------------------------------------------------------------------


def test_a_deny_is_never_waived(tmp_path, rules):
    assert "a deny is never waived" in refusal(rules, tmp_path, "Bash(gh pr review* --approve*)")


def test_a_waiver_never_touches_an_allow_rule(tmp_path, rules):
    assert "is an allow rule" in refusal(rules, tmp_path, "Bash(git status*)")


NOT_WAIVABLE = [
    "Bash(gh pr reviewing*)",  # no rule at all
    *[
        text
        for text in json.loads(permissions.TABLE.read_text(encoding="utf-8"))["ask"]["GitHub, writing what others read"]
        if text != REVIEW
    ],
]


@pytest.mark.parametrize("text", NOT_WAIVABLE)
def test_only_an_ask_the_table_marks_waivable_is_waived(tmp_path, rules, text):
    assert refusal(rules, tmp_path, text)


def test_the_comment_forms_stay_out_of_v1(rules):
    assert "Bash(gh pr comment*)" in NOT_WAIVABLE and "Bash(gh issue comment*)" in NOT_WAIVABLE
    assert not set(NOT_WAIVABLE) & set(rules.waivable)


def test_a_waiver_file_that_cannot_be_trusted_waives_nothing(tmp_path, rules):
    folder = clone(tmp_path)
    found = waivers.read(rules, waiver_file(tmp_path, entry(folder, asks=("Bash(gh secret*)",))))
    assert found.problem.endswith("it waives nothing, so every ask stays")
    run(folder, rules, found, tmp_path)
    assert REVIEW in settings(folder)["permissions"]["ask"]


def test_a_retired_ask_in_the_waiver_file_is_skipped_with_a_note(tmp_path, rules):
    folder = clone(tmp_path)
    retired = rules.retired_ask[0]
    found = waivers.read(rules, waiver_file(tmp_path, entry(folder, asks=(REVIEW, retired))))
    assert found.for_folder(folder).asks == (REVIEW,)
    assert any(retired in note and "retired" in note for note in found.lines())


# -- the file ----------------------------------------------------------------------------


def test_no_waiver_file_means_no_waivers(tmp_path, rules):
    found = waivers.read(rules, tmp_path / "missing.toml")
    assert found == waivers.Waivers() and found.lines() == []


def test_a_relative_folder_path_refuses_the_waiver_file(tmp_path, rules):
    found = waivers.read(rules, waiver_file(tmp_path, entry("some/clone")))
    assert not found.entries and "not absolute" in found.problem


@pytest.mark.parametrize("why", [None, "  "])
def test_a_waiver_without_a_why_refuses_the_waiver_file(tmp_path, rules, why):
    body = entry(clone(tmp_path)) if why is None else entry(clone(tmp_path), why=why)
    body = body.replace(f"why = {json.dumps(WHY)}\n", "") if why is None else body
    found = waivers.read(rules, waiver_file(tmp_path, body))
    assert not found.entries and "has no why" in found.problem


@pytest.mark.parametrize(
    ("body", "said"),
    [
        ('[[folder]]\npath = "/x"\nasks = "Bash(gh pr review*)"\nwhy = "x"\n', "asks"),
        ('[[folder]]\npath = "/x"\nasks = ["Bash(gh pr review*)"]\nwhy = "x"\nallow = ["x"]\n', "allow"),
        ('folder = "x"\n', "folder"),
        ("[[folder\n", "TOML"),
    ],
)
def test_a_file_that_is_not_well_formed_waives_nothing(tmp_path, rules, body, said):
    found = waivers.read(rules, waiver_file(tmp_path, body))
    assert not found.entries and said in found.problem


def test_a_folder_named_twice_refuses_the_waiver_file(tmp_path, rules):
    folder = clone(tmp_path)
    found = waivers.read(rules, waiver_file(tmp_path, entry(folder) + entry(folder)))
    assert found.for_folder(folder) is None and "twice" in found.problem


def test_a_waiver_file_that_is_not_utf_8_waives_nothing(tmp_path, rules):
    file = tmp_path / "ask-waivers.toml"
    file.write_bytes(entry(clone(tmp_path)).encode("utf-8") + b"# \xff\n")
    assert "UTF-8" in waivers.read(rules, file).problem


@POSIX
def test_a_linked_waiver_file_is_refused(tmp_path, rules):
    real = waiver_file(tmp_path, entry(clone(tmp_path)))
    link = tmp_path / "linked.toml"
    link.symlink_to(real)
    found = waivers.read(rules, link)
    assert not found.entries and found.problem


@POSIX
def test_a_pipe_in_place_of_the_waiver_file_is_refused_without_waiting(tmp_path, rules):
    fifo = tmp_path / "ask-waivers.toml"
    os.mkfifo(fifo)  # opened plainly to read, it waits for a writer that never comes
    told: list[waivers.Waivers] = []
    reader = threading.Thread(target=lambda: told.append(waivers.read(rules, fifo)), daemon=True)
    reader.start()
    reader.join(10)
    if reader.is_alive():  # let the stuck reader go before failing
        os.close(os.open(fifo, os.O_WRONLY | os.O_NONBLOCK))
    assert told and not told[0].entries and "plain file" in told[0].problem


@POSIX
def test_only_a_plain_file_is_read_as_the_waiver_file(tmp_path, rules):
    found = waivers.read(rules, clone(tmp_path, "ask-waivers.toml"))
    assert not found.entries and "not a plain file" in found.problem


def test_a_waiver_for_a_folder_that_is_gone_is_noted_once(tmp_path, rules):
    found = waivers.read(rules, waiver_file(tmp_path, entry(tmp_path / "gone")))
    assert sum("gone" in line for line in found.lines()) == 1


# -- what a run says ---------------------------------------------------------------------


def test_every_run_says_what_the_owners_waiver_kept_out_and_why(tmp_path, rules):
    folder = clone(tmp_path)
    found = waivers.read(rules, waiver_file(tmp_path, entry(folder)))
    line = run(folder, rules, found, tmp_path).line()
    assert f'kept 1 ask out by the owner\'s waiver: {REVIEW} (why: "{WHY}")' in line
    again = run(folder, rules, found, tmp_path).line()
    assert "already has every rule" in again and "kept 1 ask out" in again


def test_a_dry_run_says_what_decides_a_waived_command_once_its_ask_is_gone(tmp_path, rules):
    folder = clone(tmp_path)
    found = waivers.read(rules, waiver_file(tmp_path, entry(folder)))
    line = run(folder, rules, found, tmp_path, dry_run=True).line()
    assert f"would keep 1 ask out by the owner's waiver: {REVIEW}" in line
    assert "`gh pr review` is then decided by: Claude Code asks as it would without the rule" in line
    hooked = run(folder, rules, found, tmp_path, dry_run=True, hook="python3 -I session_hook.py").line()
    assert "decided by: your session hook" in hooked
    allowing = permissions.Rules(allow=["Bash(gh pr *)"], deny=[], ask=[REVIEW], waivable=[REVIEW])
    allowed = permissions.apply(folder, allowing, True, backup_root=tmp_path / "b", waiver=found.for_folder(folder))
    assert "decided by: the allow rule Bash(gh pr *)" in allowed.line()


def test_a_why_is_shown_without_control_characters():
    assert waivers.shown("one\ntwo\r\tthree") == '"one two  three"'
    assert waivers.shown("bell\x07 and \x1b[31mred") == '"bell and [31mred"'


def test_a_why_is_cut_to_200_characters():
    assert waivers.shown(WHY) == f'"{WHY}"'
    assert waivers.shown("x" * 250) == '"' + "x" * 200 + '"'


def test_the_runs_first_line_says_why_the_waiver_file_waives_nothing(tmp_path, capsys):
    folder = clone(tmp_path)
    (tmp_path / ".config/claude-voice").mkdir(parents=True)
    (tmp_path / ".config/claude-voice/ask-waivers.toml").write_text(
        entry(folder, asks=("Bash(gh secret*)",)), encoding="utf-8"
    )
    assert main(["permissions", str(folder)], env={"HOME": str(tmp_path)}) == 0
    out = capsys.readouterr().out.splitlines()
    assert "gh secret" in out[0] and "every ask" in out[0]
    assert REVIEW in settings(folder)["permissions"]["ask"]


def test_the_command_keeps_the_waived_ask_out(tmp_path, capsys):
    folder = clone(tmp_path)
    (tmp_path / ".config/claude-voice").mkdir(parents=True)
    (tmp_path / ".config/claude-voice/ask-waivers.toml").write_text(entry(folder), encoding="utf-8")
    assert main(["permissions", str(folder)], env={"HOME": str(tmp_path)}) == 0
    assert REVIEW not in settings(folder)["permissions"]["ask"]
    assert "kept 1 ask out" in capsys.readouterr().out
