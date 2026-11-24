"""The call log learns the owner's other machines from the bridge's own settings.

The log counts a machine's text checks as polling only for machines the bridge knows. It is
started after the bridge, so the bridge hands over the names it read from its settings file.
"""

from fakes import FakeClaude

from claude_voice import config
from claude_voice.server import build_server
from claude_voice.sessions import SessionManager
from claude_voice.store import Store


def bridge(tmp_path, settings_text):
    settings = tmp_path / "settings.toml"
    settings.write_text(settings_text, encoding="utf-8")
    (tmp_path / "live").mkdir()
    m = SessionManager(Store(tmp_path / "b.db"), project_root=tmp_path, client_factory=FakeClaude())
    return build_server(
        m,
        conversations=lambda **k: [],
        live_dir=tmp_path / "live",
        read_transcript=lambda s, d: [],
        org_folders=[],
        repo_config=config.load(settings, own_clone=None, env={"HOME": str(tmp_path)}),
        proof=True,  # starts no plugins, whatever settings it finds
    )


def test_the_call_log_knows_the_machines_named_in_the_bridges_settings(tmp_path):
    server = bridge(tmp_path, "[machines.phone-b]\naddress = '100.64.0.9'\n")
    assert server.machine_names == ["phone-b"]


def test_a_bridge_with_no_other_machines_hands_the_call_log_none(tmp_path):
    assert bridge(tmp_path, "").machine_names == []
