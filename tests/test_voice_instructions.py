"""The owner's own rules for the voice: a file outside the repository, added to the general ones."""

import logging

from fakes import FakeClaude
from mcp.client import Client

from claude_voice import config
from claude_voice.server import (
    BASE_INSTRUCTIONS,
    DEFAULT_RULES,
    INSTRUCTIONS,
    OWN_RULES_BYTES,
    build_server,
    voice_instructions,
)
from claude_voice.sessions import SessionManager
from claude_voice.store import Store

OWN = "Proposals go to the session called manager.\nAsk one question at a time."


def settings(tmp_path, line=""):
    file = tmp_path / "config.toml"
    file.write_text(line)
    return config.load(file, own_clone=None, env={"HOME": str(tmp_path)})


async def said_to_the_voice(tmp_path, repos):
    (tmp_path / "home").mkdir(exist_ok=True)
    manager = SessionManager(Store(tmp_path / "b.db"), project_root=tmp_path / "home", client_factory=FakeClaude())
    server = build_server(manager, conversations=lambda **k: [], live_dir=tmp_path / "live", repo_config=repos)
    async with Client(server) as c:
        return c.instructions or ""


async def test_the_owners_own_rules_file_reaches_the_voice(tmp_path):
    (tmp_path / "rules.md").write_text(OWN)
    repos = settings(tmp_path, "voice_instructions_file = '~/rules.md'\n")
    assert repos.voice_instructions_file == tmp_path / "rules.md"
    said = await said_to_the_voice(tmp_path, repos)
    assert said.startswith(INSTRUCTIONS)
    assert "The owner's own rules:\n" + OWN in said


def test_an_owners_rules_file_saved_on_windows_reaches_the_voice_with_plain_line_ends(tmp_path):
    """Notepad ends each line with a carriage return too; the voice gets the same text as from any other editor."""
    file = tmp_path / "rules.md"
    file.write_bytes(OWN.replace("\n", "\r\n").encode() + b"\r\n")
    said = voice_instructions(file)
    assert "\r" not in said
    assert said.endswith("The owner's own rules:\n" + OWN + "\n")


async def test_without_an_owners_rules_file_the_voice_gets_the_general_rules_only(tmp_path):
    repos = settings(tmp_path)
    assert repos.voice_instructions_file is None
    assert voice_instructions(None) == INSTRUCTIONS
    assert await said_to_the_voice(tmp_path, repos) == INSTRUCTIONS


def test_an_owners_rules_file_that_cannot_be_read_is_logged_and_the_bridge_runs_on(tmp_path, caplog):
    missing = tmp_path / "nowhere" / "rules.md"
    with caplog.at_level(logging.WARNING, logger="claude_voice"):
        assert voice_instructions(missing) == INSTRUCTIONS
    lines = [r.getMessage() for r in caplog.records]
    assert len(lines) == 1
    assert str(missing) in lines[0] and "voice_instructions_file" in lines[0]


def test_an_owners_rules_file_over_eight_kilobytes_is_cut(tmp_path, caplog):
    file = tmp_path / "rules.md"
    file.write_text("a" * OWN_RULES_BYTES + "TAIL")
    with caplog.at_level(logging.WARNING, logger="claude_voice"):
        said = voice_instructions(file)
    assert OWN_RULES_BYTES == 8 * 1024
    assert "a" * OWN_RULES_BYTES in said and "TAIL" not in said
    lines = [r.getMessage() for r in caplog.records]
    assert len(lines) == 1 and str(file) in lines[0] and "cut" in lines[0]


def test_the_general_rules_tell_the_voice_to_pass_on_what_it_cannot_do_itself():
    """Opening a file or running a command is a session's work: the voice sends it there at once."""
    said = " ".join(INSTRUCTIONS.split())
    assert "cannot do yourself" in said and "never say you cannot" in said
    assert "send_task" in said and "message_active_session" in said and "say briefly that it was sent" in said


def test_the_general_rules_read_out_a_next_step_and_leave_typed_commands_to_the_owner():
    said = " ".join(INSTRUCTIONS.split())
    assert "says what to do next, read that step out" in said
    assert "plain terminal; never run it for them" in said


def test_the_general_rules_read_a_question_out_and_answer_only_a_clear_choice():
    """A session's choice box, waiting for the owner: read out with numbers, answered on his word."""
    said = " ".join(INSTRUCTIONS.split())
    assert "needs_answer" in said and "list_pending_approvals" in said
    assert "options with their numbers" in said
    assert "answer_question only when the user clearly picks one" in said
    assert "never guess" in said and "on the screen" in said


def test_the_default_rules_are_named_and_each_reaches_the_voice_on_a_line_of_its_own():
    """A later setting switches each one by name, so the names are part of the design."""
    assert set(DEFAULT_RULES) == {"read_next_step", "plain_terminal", "delegate_unable", "answer_questions"}
    assert INSTRUCTIONS.startswith(BASE_INSTRUCTIONS)
    lines = INSTRUCTIONS[len(BASE_INSTRUCTIONS) :].splitlines()
    assert lines == list(DEFAULT_RULES.values())
