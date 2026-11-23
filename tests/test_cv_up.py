"""cv up: a role whose session has ended is brought back in its own tmux window, started from
the owner's role definition and never from what it last ran with (worker-on-demand-v1, PR 1)."""

import json
import os
import sys

import pytest
from claude_agent_sdk import project_key_for_directory

from claude_voice import cv, org, roster

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="cv up starts claude in tmux, which is Unix only")

ID = "0f8fad5b-d9cb-469f-a165-70867728950e"
NOW = "2026-11-16T10:00+00:00"
SHOP = (
    '[programme]\ncode = "shop"\nname = "Web shop"\n'
    '[components.site]\nfolders = ["."]\n'
    '[roles.builder]\nname = "Builder"\nsessions = ["builder-8d"]\n'
)


@pytest.fixture
def folder(tmp_path):
    shop = tmp_path / "src" / "shop"
    (shop / ".claude").mkdir(parents=True)
    (shop / ".claude" / "org.toml").write_text(SHOP, encoding="utf-8")
    return shop


def chart(*folders):
    return org.load(list(folders), remotes=lambda f: ())


class Tmux:
    def __init__(self, fails=False):
        self.ran, self.fails = [], fails

    def __call__(self, command):
        self.ran.append(command)
        if self.fails:
            import subprocess

            raise subprocess.CalledProcessError(1, command, stderr="can't find session: shop")
        return ""


def transcript(tmp_path, folder, conversation, cwd=None, key_folder=None):
    """A conversation's transcript where Claude Code keeps it: a record with no cwd, then the
    first record that carries one."""
    key = project_key_for_directory(str(key_folder or folder))
    path = tmp_path / "projects" / key / f"{conversation}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [{"type": "custom-title", "customTitle": "x"}, {"type": "user", "cwd": str(cwd or folder)}]
    lines += [{"type": "user", "cwd": "/somewhere/else"}]
    path.write_text("".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8")
    return path


def start(
    tmp_path,
    folder,
    table=None,
    the_chart=None,
    claude="/opt/claude",
    tmux="/usr/bin/tmux",
    live=(),
    recorded=None,
):
    waited = []
    transcript(tmp_path, folder, ID)
    records = {"builder-8d": ID} if recorded is None else recorded
    how = cv.Start(
        table={"builder-8d": {"folder": str(folder)}} if table is None else table,
        chart=chart(folder) if the_chart is None else the_chart,
        claude=claude,
        tmux=tmux,
        locks=tmp_path / "locks",
        live=lambda: set(live),
        wait=waited.append,
        projects=tmp_path / "projects",
        record=records.get,
    )
    return how, waited


def row(argv=(), conversation=ID, folder="", role="builder-8d"):
    return roster.Row("shop", role, folder, conversation, "shop:@1.%1", NOW, tuple(argv))


def test_a_gone_role_is_brought_back_from_its_definition(tmp_path, folder):
    how, waited = start(tmp_path, folder)
    tmux = Tmux()
    said = cv.up(row(), how, tmux)
    claude = os.path.realpath("/opt/claude")
    argv = [claude, "--resume", ID, "--model", "claude-opus-5-5", "--name", "builder-8d"]
    assert tmux.ran == [
        ["/usr/bin/tmux", "new-window", "-d", "-t", "shop:", "-n", "builder-8d", "-c", str(folder), "--", *argv]
    ]
    assert waited == [ID]
    assert said == ["builder-8d: started in tmux shop:builder-8d; follow it with `cv peek builder-8d`"]


def test_a_conversation_that_is_running_is_not_started_again(tmp_path, folder):
    how, _ = start(tmp_path, folder, live=[ID])
    tmux = Tmux()
    with pytest.raises(cv.Refused, match="is running"):
        cv.up(row(), how, tmux)
    assert tmux.ran == []


def test_a_role_with_no_definition_is_refused(tmp_path, folder):
    how, _ = start(tmp_path, folder, table={})
    with pytest.raises(cv.Refused, match="no role definition for builder-8d.*start_role"):
        cv.up(row(), how, Tmux())


def test_a_row_without_a_valid_conversation_id_is_refused(tmp_path, folder):
    how, _ = start(tmp_path, folder)
    with pytest.raises(cv.Refused, match="no valid conversation id"):
        cv.up(row(conversation="x --add-dir /"), how, Tmux())


def test_a_role_whose_name_cannot_name_a_window_is_refused(tmp_path, folder):
    how, _ = start(tmp_path, folder, table={"Builder 8d": {"folder": str(folder)}})
    with pytest.raises(cv.Refused, match="cannot name a tmux"):
        cv.up(row(role="Builder 8d"), how, Tmux())


def test_the_folder_comes_from_the_definition_and_a_row_s_other_folder_is_named(tmp_path, folder):
    how, _ = start(tmp_path, folder)
    tmux = Tmux()
    recorded = ["/opt/claude", "--resume", ID, "--model", "claude-opus-5-5", "--name", "builder-8d"]
    said = cv.up(row(argv=recorded, folder="/elsewhere"), how, tmux)
    assert tmux.ran[0][tmux.ran[0].index("-c") + 1] == str(folder)
    assert said[0] == (
        "builder-8d: the last start differed from the role definition: changed folder; started from the definition."
    )


def test_a_recorded_extra_flag_is_dropped_and_named(tmp_path, folder):
    how, _ = start(tmp_path, folder)
    tmux = Tmux()
    recorded = ["/opt/claude", "--resume", ID, "--add-dir", "/", "--model", "claude-opus-5-5", "--name", "builder-8d"]
    said = cv.up(row(argv=recorded, folder=str(folder)), how, tmux)
    assert "--add-dir" not in tmux.ran[0]
    assert said[0].endswith("extra --add-dir; started from the definition.")


def test_a_role_s_own_settings_are_passed_whole(tmp_path, folder, monkeypatch):
    monkeypatch.setattr(cv.roles, "settings_for", lambda spec, name: '{"hooks": {}}')
    how, _ = start(tmp_path, folder)
    tmux = Tmux()
    cv.up(row(), how, tmux)
    command = tmux.ran[0]
    assert command[command.index("--settings") + 1] == '{"hooks": {}}'


def test_a_role_whose_settings_cannot_be_read_is_refused(tmp_path, folder, monkeypatch):
    def missing(spec, name):
        raise LookupError("The role's settings file /x is missing")

    monkeypatch.setattr(cv.roles, "settings_for", missing)
    how, _ = start(tmp_path, folder)
    with pytest.raises(cv.Refused, match="settings file /x is missing"):
        cv.up(row(), how, Tmux())


def test_a_role_in_no_programme_is_refused(tmp_path, folder):
    how, _ = start(tmp_path, folder, the_chart=chart())
    with pytest.raises(cv.Refused, match="in no programme"):
        cv.up(row(), how, Tmux())


def test_a_role_started_by_cv_up_sits_in_exactly_one_programme(tmp_path, folder):
    other = tmp_path / "src" / "acme"
    (other / ".claude").mkdir(parents=True)
    (other / ".claude" / "org.toml").write_text(
        '[programme]\ncode = "acme"\n[roles.builder]\nsessions = ["builder-8d"]\n', encoding="utf-8"
    )
    how, _ = start(tmp_path, folder, the_chart=chart(other, folder))
    with pytest.raises(cv.Refused, match=r"more than one programme \(acme, shop\)"):
        cv.up(row(), how, Tmux())


def test_a_role_named_by_two_programmes_is_refused_even_outside_their_folders(tmp_path, folder):
    lone = tmp_path / "lone"
    lone.mkdir()
    for code in ("acme", "zeta"):
        repo = tmp_path / "src" / code
        (repo / ".claude").mkdir(parents=True)
        (repo / ".claude" / "org.toml").write_text(
            f'[programme]\ncode = "{code}"\n[roles.builder]\nsessions = ["builder-8d"]\n', encoding="utf-8"
        )
    the_chart = chart(tmp_path / "src" / "acme", tmp_path / "src" / "zeta")
    how, _ = start(tmp_path, lone, the_chart=the_chart)
    with pytest.raises(cv.Refused, match=r"more than one programme \(acme, zeta\)"):
        cv.up(row(), how, Tmux())


# -- the conversation is the role's own (quality's b1) --------------------------------------


OTHER = "1b9d6bcd-bbfd-4b2d-9b5d-ab8dfbbd4bed"


def test_a_row_pointing_at_another_role_s_conversation_is_refused(tmp_path, folder):
    how, _ = start(tmp_path, folder)
    transcript(tmp_path, folder, OTHER)
    tmux = Tmux()
    with pytest.raises(cv.Refused, match="conversation changed since start_role .*1b9d6bcd.*0f8fad5b.*start it fresh"):
        cv.up(row(conversation=OTHER), how, tmux)
    assert tmux.ran == []


def test_a_role_with_no_record_is_refused(tmp_path, folder):
    how, _ = start(tmp_path, folder, recorded={})
    with pytest.raises(
        cv.Refused, match="start builder-8d once with start_role, so the bridge records its conversation"
    ):
        cv.up(row(), how, Tmux())


def test_a_conversation_with_no_transcript_in_the_role_s_folder_is_refused(tmp_path, folder):
    elsewhere = tmp_path / "src" / "other"
    elsewhere.mkdir()
    how, _ = start(tmp_path, folder, recorded={"builder-8d": OTHER})
    transcript(tmp_path, elsewhere, OTHER)
    with pytest.raises(cv.Refused, match="no transcript"):
        cv.up(row(conversation=OTHER), how, Tmux())


def test_a_transcript_that_is_a_link_is_refused(tmp_path, folder):
    elsewhere = tmp_path / "src" / "other"
    elsewhere.mkdir()
    how, _ = start(tmp_path, folder, recorded={"builder-8d": OTHER})
    real = transcript(tmp_path, elsewhere, OTHER, cwd=folder)
    (tmp_path / "projects" / project_key_for_directory(str(folder)) / f"{OTHER}.jsonl").symlink_to(real)
    with pytest.raises(cv.Refused, match="not a plain file"):
        cv.up(row(conversation=OTHER), how, Tmux())


def test_a_conversation_that_began_in_another_folder_is_refused(tmp_path, folder):
    how, _ = start(tmp_path, folder)
    transcript(tmp_path, folder, ID, cwd="/somewhere/else")
    with pytest.raises(cv.Refused, match="began in another folder"):
        cv.up(row(), how, Tmux())


def test_a_role_folder_behind_a_link_is_judged_by_its_real_folder(tmp_path, folder):
    """Claude Code keys a transcript by the real folder, so a link in the definition must not hide it."""
    link = tmp_path / "linked"
    link.symlink_to(folder, target_is_directory=True)
    how, _ = start(tmp_path, folder, table={"builder-8d": {"folder": str(link)}})
    assert cv.up(row(), how, Tmux())[-1].startswith("builder-8d: started in tmux shop:builder-8d")


def test_only_launch_role_and_launch_approver_write_a_role_record():
    """The record cv up trusts is written by the bridge's own launchers alone (v1.4 §1)."""
    import ast
    from pathlib import Path

    writers = set()

    def visit(node, where, module):
        for child in ast.iter_child_nodes(node):
            inner = child.name if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) else where
            if (
                isinstance(child, ast.Call)
                and getattr(child.func, "attr", "") == "set_setting"
                and child.args
                and "role:" in ast.unparse(child.args[0])
            ):
                writers.add(f"{module}:{where}")
            visit(child, inner, module)

    for path in Path(cv.__file__).parent.glob("*.py"):
        visit(ast.parse(path.read_text(encoding="utf-8")), "<module>", path.name)
    assert writers and writers <= {"server.py:launch_role", "server.py:launch_approver"}


def test_the_bridge_record_is_read_without_being_able_to_write(tmp_path):
    import sqlite3

    from claude_voice import relaunch

    db = tmp_path / "bridge.db"
    with sqlite3.connect(db) as made:
        made.execute("CREATE TABLE settings(key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at REAL NOT NULL)")
        made.execute("INSERT INTO settings VALUES ('role:builder-8d', ?, 0)", (json.dumps({"id": ID, "pid": 1}),))
    assert relaunch.recorded_conversation(db, "builder-8d") == ID
    assert relaunch.recorded_conversation(db, "builder-9") is None
    assert relaunch.recorded_conversation(tmp_path / "none.db", "builder-8d") is None
    with relaunch.read_only(db) as handle, pytest.raises(sqlite3.OperationalError, match="readonly"):
        handle.execute("DELETE FROM settings")


def test_without_claude_or_tmux_nothing_is_started(tmp_path, folder):
    for missing in ({"claude": None}, {"tmux": None}):
        how, _ = start(tmp_path, folder, **missing)
        with pytest.raises(cv.Refused, match="needs tmux and claude"):
            cv.up(row(), how, Tmux())


def test_what_tmux_says_when_it_cannot_open_the_window_is_shown(tmp_path, folder):
    how, waited = start(tmp_path, folder)
    with pytest.raises(cv.Refused, match="can't find session: shop"):
        cv.up(row(), how, Tmux(fails=True))
    assert waited == []


def test_a_second_start_while_the_first_is_starting_is_refused(tmp_path, folder):
    how, _ = start(tmp_path, folder)
    tmux = Tmux()
    second = []

    def wait(conversation):
        try:
            cv.up(row(), how, tmux)
        except cv.Refused as exc:
            second.append(str(exc))

    cv.up(row(), cv.Start(**{**how.__dict__, "wait": wait}), tmux)
    assert len(tmux.ran) == 1
    assert second and "being started" in second[0]


def test_cv_up_is_a_command_of_cv(tmp_path, monkeypatch, capsys):
    home = tmp_path / "home"
    (home / ".claude" / "sessions").mkdir(parents=True)
    roster.write(home / ".local" / "state" / "claude-voice" / "sessions.toml", [row()])
    monkeypatch.setattr(cv, "up", lambda r, how, run: [f"up {r.role}"])
    monkeypatch.setattr(cv, "_start", lambda home, env: None)
    assert cv.main(["up", "builder-8d"], env={"HOME": str(home)}) == 0
    assert capsys.readouterr().out == "up builder-8d\n"
