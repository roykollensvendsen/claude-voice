"""Where the bridge finds the tools it runs."""

from __future__ import annotations

import claude_agent_sdk
import pytest

from claude_voice import procs


@pytest.fixture
def sdk(tmp_path, monkeypatch):
    """The SDK's folder, empty until a test puts a bundled copy in it."""
    monkeypatch.setattr(claude_agent_sdk, "__file__", str(tmp_path / "__init__.py"))
    return tmp_path


def bundle(sdk):
    copy = sdk / "_bundled" / "claude"
    copy.parent.mkdir()
    copy.write_text("")
    return str(copy)


def test_the_claude_program_is_the_owners_own_first_and_the_bundled_copy_only_if_it_is_there(sdk, monkeypatch):
    """#286 item 2: one helper, one order, and the bundled copy is used only when it exists."""
    monkeypatch.setattr(procs, "find", lambda name: "/home/owner/.local/bin/claude" if name == "claude" else None)
    assert procs.claude() == "/home/owner/.local/bin/claude"
    monkeypatch.setattr(procs, "find", lambda name: None)
    assert procs.claude() is None  # no bundled copy either
    copy = bundle(sdk)
    assert procs.claude() == copy


async def test_a_background_session_cannot_start_or_stop_without_claude_code(sdk, monkeypatch):
    """A missing program is said plainly, not left to fail as a bundled path that is not there."""
    from claude_voice import transcripts

    monkeypatch.setattr(procs, "find", lambda name: None)
    with pytest.raises(RuntimeError, match="Claude Code was not found"):
        await transcripts.stop_background("sid-1")
    with pytest.raises(RuntimeError, match="Claude Code was not found"):
        await transcripts.start_background(str(sdk), "worker")
