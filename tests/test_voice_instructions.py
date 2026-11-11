"""The owner's own rules for the voice: a file outside the repository, added to the general ones."""

import logging

from fakes import FakeClaude
from mcp.client import Client

from claude_voice import config
from claude_voice.server import INSTRUCTIONS, OWN_RULES_BYTES, build_server, voice_instructions
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
