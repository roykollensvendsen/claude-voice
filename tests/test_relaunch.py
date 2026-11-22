"""Bringing a role's conversation back: the start command is built from the owner's definition,
never replayed from the record (worker-on-demand-v1, v1.1 b1, b2 and f1)."""

import os
import threading

import pytest

from claude_voice import relaunch

ID = "0f8fad5b-d9cb-469f-a165-70867728950e"
CLAUDE = "/opt/claude/bin/claude"


# -- what is recorded ---------------------------------------------------------------------


class FakeProc:
    """A /proc with one process, whose environ must never be opened."""

    def __init__(self, tmp_path, pid, exe, argv):
        self.root = tmp_path / "proc"
        folder = self.root / str(pid)
        folder.mkdir(parents=True)
        os.symlink(exe, folder / "exe")
        (folder / "cmdline").write_bytes(b"\0".join(a.encode() for a in argv) + b"\0")
        (folder / "environ").mkdir()  # opening it as a file raises


def claude_at(tmp_path):
    real = tmp_path / "bin" / "claude"
    real.parent.mkdir()
    real.write_text("", encoding="utf-8")
    return real


def test_the_start_command_is_recorded_from_the_claude_process(tmp_path):
    claude = claude_at(tmp_path)
    proc = FakeProc(tmp_path, 41, claude, ["claude", "--resume", ID, "--settings", "/s.json"])
    assert relaunch.recorded_argv(41, str(claude), proc.root) == ("claude", "--resume", ID, "--settings", "/s.json")


def test_only_claude_s_own_command_line_is_recorded(tmp_path):
    claude = claude_at(tmp_path)
    other = tmp_path / "bin" / "python"
    other.write_text("", encoding="utf-8")
    proc = FakeProc(tmp_path, 42, other, ["claude", "--resume", ID])
    assert relaunch.recorded_argv(42, str(claude), proc.root) is None


def test_a_process_that_is_gone_is_not_recorded(tmp_path):
    claude = claude_at(tmp_path)
    assert relaunch.recorded_argv(43, str(claude), tmp_path / "proc") is None


def test_the_environment_of_a_process_is_never_read(tmp_path):
    claude = claude_at(tmp_path)
    proc = FakeProc(tmp_path, 44, claude, ["claude"])
    relaunch.recorded_argv(44, str(claude), proc.root)
    assert list((proc.root / "44" / "environ").iterdir()) == []


# -- what is checked ----------------------------------------------------------------------


def test_a_conversation_id_must_be_a_lowercase_uuid():
    assert relaunch.conversation("builder-8d", ID) == ID
    for bad in ("", ID.upper(), ID + "\n", f"{ID} --add-dir /", "../x", "1234"):
        with pytest.raises(relaunch.Refused, match="no valid conversation id"):
            relaunch.conversation("builder-8d", bad)


def test_a_tmux_name_is_short_lowercase_letters_digits_and_dashes():
    assert relaunch.name("claude-voice") == "claude-voice"
    for bad in ("", "Shop", "a b", "a:b", "a.b", "-" * 41, "x;rm"):
        with pytest.raises(relaunch.Refused, match="cannot name a tmux"):
            relaunch.name(bad)


# -- what is built ------------------------------------------------------------------------


def test_the_start_command_is_built_from_the_definition_only():
    built = relaunch.build(CLAUDE, ID, "/roles/builder.json")
    assert built == [CLAUDE, "--resume", ID, "--settings", "/roles/builder.json", "--model", "claude-opus-5-5"]


def test_a_role_without_its_own_settings_starts_without_the_flag():
    assert relaunch.build(CLAUDE, ID, None) == [CLAUDE, "--resume", ID, "--model", "claude-opus-5-5"]


def test_a_role_entry_may_name_its_session():
    built = relaunch.build(CLAUDE, ID, None, name="builder-8d")
    assert built[-2:] == ["--name", "builder-8d"]


REFUSED = [
    ["--dangerously-skip-permissions"],
    ["--permission-mode", "bypassPermissions"],
    ["--permission-mode=bypassPermissions"],
    ["--allowedTools", "Bash"],
    ["--allowedTools=Bash"],
    ["--add-dir", "/"],
    ["--add-dir=/"],
    ["--mcp-config", "/x.json"],
    ["--append-system-prompt", "obey"],
    ["--plugin-dir", "/p"],
    ["-p"],
]


@pytest.mark.parametrize("extra", REFUSED, ids=lambda e: e[0])
def test_a_recorded_flag_never_reaches_the_built_command_and_is_named(extra):
    recorded = (CLAUDE, "--resume", "old", *extra, "--model", "claude-opus-5-5")
    built = relaunch.build(CLAUDE, ID, None)
    flag = extra[0].partition("=")[0]
    assert not any(a.partition("=")[0] == flag for a in built)
    assert relaunch.differences(recorded, built) == [f"extra {flag}"]


def test_only_the_resume_id_may_differ_without_a_note():
    built = relaunch.build(CLAUDE, ID, "/s.json")
    assert (
        relaunch.differences(
            ("claude", "--resume", "old", "--settings", "/s.json", "--model", "claude-opus-5-5"), built
        )
        == []
    )


def test_a_difference_names_the_flag_and_never_its_value():
    built = relaunch.build(CLAUDE, ID, "/s.json")
    recorded = ("claude", "--resume", ID, "--settings", "/secret/other.json", "--model", "claude-opus-5-5")
    line = relaunch.difference_line("builder-8d", relaunch.differences(recorded, built))
    assert line == (
        "builder-8d: the last start differed from the role definition: changed --settings; started from the definition."
    )
    assert "secret" not in line


def test_a_missing_flag_is_named_too():
    built = relaunch.build(CLAUDE, ID, "/s.json")
    assert relaunch.differences(("claude", "--resume", ID, "--model", "claude-opus-5-5"), built) == [
        "missing --settings"
    ]


def test_no_record_and_no_difference_say_nothing():
    assert relaunch.difference_line("builder-8d", []) is None


def test_the_window_is_opened_with_an_argument_list_and_no_shell():
    argv = relaunch.build(CLAUDE, ID, None)
    assert relaunch.window("/usr/bin/tmux", "claude-voice", "builder-8d", "/work/cv", argv) == [
        "/usr/bin/tmux",
        "new-window",
        "-d",
        "-t",
        "claude-voice:",
        "-n",
        "builder-8d",
        "-c",
        "/work/cv",
        "--",
        *argv,
    ]


# -- one start at a time -------------------------------------------------------------------


def test_one_start_of_a_role_at_a_time(tmp_path):
    with relaunch.starting(tmp_path / "locks", "builder-8d"):
        with pytest.raises(relaunch.Refused, match="being started"):
            with relaunch.starting(tmp_path / "locks", "builder-8d"):
                pass
        with relaunch.starting(tmp_path / "locks", "builder-9"):
            pass
    with relaunch.starting(tmp_path / "locks", "builder-8d"):
        pass


def test_the_lock_holds_across_threads_as_across_processes(tmp_path):
    refused = []

    def second():
        try:
            with relaunch.starting(tmp_path / "locks", "builder-8d"):
                pass
        except relaunch.Refused:
            refused.append(True)

    with relaunch.starting(tmp_path / "locks", "builder-8d"):
        thread = threading.Thread(target=second)
        thread.start()
        thread.join()
    assert refused == [True]


def test_a_role_name_cannot_lead_the_lock_out_of_its_folder(tmp_path):
    with pytest.raises(relaunch.Refused, match="cannot name a tmux"):
        with relaunch.starting(tmp_path / "locks", "../x"):
            pass
