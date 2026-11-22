"""cv up: a role whose session has ended is brought back in its own tmux window, started from
the owner's role definition and never from what it last ran with (worker-on-demand-v1, PR 1)."""

import os

import pytest

from claude_voice import cv, org, roster

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


def start(tmp_path, folder, table=None, the_chart=None, claude="/opt/claude", tmux="/usr/bin/tmux", live=()):
    waited = []
    how = cv.Start(
        table={"builder-8d": {"folder": str(folder)}} if table is None else table,
        chart=chart(folder) if the_chart is None else the_chart,
        claude=claude,
        tmux=tmux,
        locks=tmp_path / "locks",
        live=lambda: set(live),
        wait=waited.append,
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
    with pytest.raises(cv.Refused, match="both shop and acme"):
        cv.up(row(), how, Tmux())


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
