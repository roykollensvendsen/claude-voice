"""The Stop hook that will not let a turn end with a peer's message unanswered, once."""

import importlib.util
import io
import json
import pathlib

import pytest

ROOT = pathlib.Path(__file__).parent.parent


def _hook():
    spec = importlib.util.spec_from_file_location("unanswered_peer", ROOT / "scripts/hooks/unanswered_peer.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def peer(name, text, address=None):
    address = address or f"uds:/run/user/1000/cc-socks/{abs(hash(name)) % 99999}.sock"
    body = f'<cross-session-message from="{address}" from-name="{name}" from-mode="prompting">\n{text}\n'
    return {
        "type": "user",
        "message": {
            "role": "user",
            "content": f"Another Claude session sent a message:\n{body}</cross-session-message>",
        },
    }


def queued(name, text):
    entry = peer(name, text)
    return {"type": "attachment", "attachment": {"type": "queued_command", "prompt": entry["message"]["content"]}}


def reply(to):
    return {
        "type": "assistant",
        "message": {
            "role": "assistant",
            "content": [{"type": "tool_use", "name": "SendMessage", "input": {"to": to, "message": "ok"}}],
        },
    }


def run(tmp_path, entries, active=False, monkeypatch=None):
    transcript = tmp_path / "t.jsonl"
    transcript.write_text("\n".join(json.dumps(e) for e in entries) + "\n")
    hook = _hook()
    if monkeypatch:
        monkeypatch.setattr(hook, "STATE_DIR", tmp_path / "state")
    stdin = io.StringIO(
        json.dumps({"session_id": "s1", "transcript_path": str(transcript), "stop_hook_active": active})
    )
    out = io.StringIO()
    hook.main(stdin, out)
    return json.loads(out.getvalue()) if out.getvalue().strip() else None


@pytest.fixture
def state(tmp_path, monkeypatch):
    return monkeypatch


def test_an_unanswered_peer_message_blocks_the_end_of_the_turn(tmp_path, state):
    verdict = run(tmp_path, [peer("voice-integration-87", "Can we test now?")], monkeypatch=state)
    assert verdict["decision"] == "block"
    assert "voice-integration-87" in verdict["reason"]
    assert "Can we test now?" in verdict["reason"]


def test_a_reply_by_name_or_address_counts(tmp_path, state):
    by_name = [peer("voice-integration-87", "hi"), reply("voice-integration-87")]
    by_address = [peer("other", "hi", address="uds:/x.sock"), reply("uds:/x.sock")]
    assert run(tmp_path, by_name, monkeypatch=state) is None
    assert run(tmp_path, by_address, monkeypatch=state) is None


def test_a_reply_sent_before_the_message_does_not_count(tmp_path, state):
    entries = [reply("voice-integration-87"), peer("voice-integration-87", "new question")]
    assert run(tmp_path, entries, monkeypatch=state)["decision"] == "block"


def test_a_message_waiting_in_the_queue_counts_too(tmp_path, state):
    assert run(tmp_path, [queued("voice-integration-87", "queued")], monkeypatch=state)["decision"] == "block"


def test_the_second_stop_in_a_row_is_always_let_through(tmp_path, state):
    assert run(tmp_path, [peer("voice-integration-87", "hi")], active=True, monkeypatch=state) is None


def test_each_message_blocks_only_once_ever(tmp_path, state):
    entries = [peer("voice-integration-87", "hi")]
    assert run(tmp_path, entries, monkeypatch=state)["decision"] == "block"
    assert run(tmp_path, entries, monkeypatch=state) is None
    entries.append(peer("voice-integration-87", "and another"))
    assert run(tmp_path, entries, monkeypatch=state)["decision"] == "block"


def test_couriers_that_cannot_receive_a_reply_are_ignored(tmp_path, state):
    assert run(tmp_path, [peer("claude-voice-msg-ab12cd-34", "status?")], monkeypatch=state) is None


def test_a_missing_or_broken_transcript_never_blocks(tmp_path, state):
    hook = _hook()
    out = io.StringIO()
    hook.main(io.StringIO(json.dumps({"transcript_path": str(tmp_path / "nope.jsonl")})), out)
    assert out.getvalue().strip() == ""
    out = io.StringIO()
    hook.main(io.StringIO("not json"), out)
    assert out.getvalue().strip() == ""


def test_a_courier_known_by_its_folder_is_ignored_whatever_its_name(tmp_path, state):
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    (sessions / "4242.json").write_text(
        json.dumps({"pid": 4242, "cwd": "/tmp/claude-voice-msg-abc", "name": "Roy via stemmen"})
    )
    hook = _hook()
    state.setattr(hook, "SESSIONS_DIR", sessions)
    entry = peer("Roy via stemmen", "hva skjer?", address="uds:/run/user/1000/cc-socks/4242.sock")
    transcript = tmp_path / "t.jsonl"
    transcript.write_text(json.dumps(entry) + "\n")
    state.setattr(hook, "STATE_DIR", tmp_path / "state")
    out = io.StringIO()
    hook.main(io.StringIO(json.dumps({"session_id": "s", "transcript_path": str(transcript)})), out)
    assert out.getvalue().strip() == ""


def test_the_configured_courier_name_is_ignored_even_after_the_courier_is_gone(tmp_path, state):
    env = tmp_path / "env"
    env.write_text("CLAUDE_VOICE_TOKEN=secret\nCLAUDE_VOICE_COURIER_NAME=Roy via stemmen\n")
    hook = _hook()
    state.setattr(hook, "ENV_FILE", env)
    state.setattr(hook, "SESSIONS_DIR", tmp_path / "no-such-dir")  # the courier's process is gone
    state.setattr(hook, "STATE_DIR", tmp_path / "state")
    transcript = tmp_path / "t.jsonl"
    entry = peer("Roy via stemmen", "hva skjer?", address="uds:/run/user/1000/cc-socks/999.sock")
    transcript.write_text(json.dumps(entry) + "\n")
    out = io.StringIO()
    hook.main(io.StringIO(json.dumps({"session_id": "s", "transcript_path": str(transcript)})), out)
    assert out.getvalue().strip() == ""
