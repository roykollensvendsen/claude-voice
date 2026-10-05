"""One test per rule marked in the source, named in the rule's own words.

scripts/mutate.py switches each rule off and checks that its test goes red.
"""

import asyncio

import pytest
from claude_agent_sdk import PermissionResultDeny, ToolPermissionContext

from claude_voice.approvals import ApprovalBroker
from claude_voice.server import ConfigError, load_config
from claude_voice.sessions import SessionManager
from claude_voice.store import Store


def test_an_api_key_in_the_environment_stops_the_bridge_from_starting(tmp_path):
    with pytest.raises(ConfigError, match="ANTHROPIC_API_KEY"):
        load_config({"CLAUDE_VOICE_ROOT": str(tmp_path), "ANTHROPIC_API_KEY": "sk-x"}, "stdio")


def test_a_project_outside_the_root_is_refused(tmp_path):
    (tmp_path / "root").mkdir()
    (tmp_path / "outside").mkdir()
    m = SessionManager(Store(tmp_path / "b.db"), project_root=tmp_path / "root")
    with pytest.raises(ValueError, match="under"):
        m.resolve_project(str(tmp_path / "outside"))


def test_an_approval_nobody_answers_is_refused(tmp_path):
    store = Store(tmp_path / "b.db")
    session = store.create_session(str(tmp_path))
    ask = ApprovalBroker(store, timeout_seconds=0.01).callback(session["id"])
    outcome = asyncio.run(ask("Bash", {"command": "rm -rf build"}, ToolPermissionContext()))
    assert isinstance(outcome, PermissionResultDeny)
