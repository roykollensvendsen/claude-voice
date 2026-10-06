"""Reading Claude Code's own transcript file, including messages that arrive mid-turn."""

import json

from claude_agent_sdk import project_key_for_directory

from claude_voice.transcripts import read_raw_transcript, recent_turns


def test_peer_messages_that_arrive_mid_turn_are_kept_and_other_meta_entries_are_not(tmp_path):
    cwd = "/home/someone/project"
    lines = [
        {"type": "user", "message": {"role": "user", "content": "Fix the export"}},
        {
            "type": "user",
            "isMeta": True,
            "message": {"role": "user", "content": "<system-reminder>noise</system-reminder>"},
        },
        {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "Working."}]}},
        {
            "type": "user",
            "isMeta": True,
            "origin": {"kind": "peer", "name": "Roy via stemmen"},
            "message": {"role": "user", "content": "Another Claude session sent a message:\nWhich folder?"},
        },
        {"type": "assistant", "isSidechain": True, "message": {"content": [{"type": "text", "text": "subagent"}]}},
        {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "In billing."}]}},
        {"type": "queue-operation", "operation": "enqueue", "content": "ignored"},
    ]
    path = tmp_path / project_key_for_directory(cwd) / "sid-1.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text("".join(json.dumps(x) + "\n" for x in lines) + '{"type": "user", "trunc')

    turns = recent_turns(read_raw_transcript("sid-1", cwd, projects_dir=tmp_path), None)
    assert [(t["role"], t["text"]) for t in turns] == [
        ("user", "Fix the export"),
        ("assistant", "Working."),
        ("user", "Another Claude session sent a message:\nWhich folder?"),
        ("assistant", "In billing."),
    ]


def test_a_missing_transcript_reads_as_empty(tmp_path):
    assert read_raw_transcript("nope", "/x", projects_dir=tmp_path) == []
