"""Reading Claude Code's own transcript file, including messages that arrive mid-turn."""

import json
from types import SimpleNamespace

from claude_agent_sdk import project_key_for_directory

from claude_voice import transcripts
from claude_voice.transcripts import read_raw_transcript, recent_turns


def test_a_transcript_is_read_as_utf8_whatever_the_system_encoding(tmp_path):
    # Claude Code writes UTF-8. Windows reads text as cp1252 by default, where
    # the last byte of a closing quote (E2 80 9D) is not a character at all.
    cwd = "/home/someone/project"
    text = "Renamed “Café del Mar” – done ✓"
    path = tmp_path / project_key_for_directory(cwd) / "sid-1.jsonl"
    path.parent.mkdir(parents=True)
    entry = {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}}
    path.write_bytes((json.dumps(entry, ensure_ascii=False) + "\n").encode("utf-8"))

    turns = recent_turns(read_raw_transcript("sid-1", cwd, projects_dir=tmp_path), None)
    assert [t["text"] for t in turns] == [text]


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


def write(path, entries, mode="w"):
    with path.open(mode, encoding="utf-8") as f:
        f.writelines(json.dumps(e) + "\n" for e in entries)


def test_a_transcript_keeps_only_what_a_person_reads_not_tool_output(tmp_path):
    """Transcripts reach hundreds of megabytes, nearly all of it tool output nobody reads back."""
    path = tmp_path / project_key_for_directory("/w") / "s.jsonl"
    path.parent.mkdir(parents=True)
    huge = "x" * 200_000
    write(
        path,
        [
            {"type": "user", "message": {"content": "Run the tests"}},
            {
                "type": "assistant",
                "message": {"content": [{"type": "tool_use", "id": "t", "name": "Bash", "input": {"command": huge}}]},
            },
            {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t", "content": huge}]}},
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "All green."}]}},
        ],
    )
    msgs = transcripts.read_raw_transcript("s", "/w", tmp_path)
    assert huge not in json.dumps([m.message for m in msgs])
    assert transcripts.recent_turns(msgs, None) == [
        {"role": "user", "text": "Run the tests"},
        {"role": "assistant", "text": "All green.", "tools": ["Bash"]},
    ]


def test_a_growing_transcript_is_read_from_where_it_left_off(tmp_path):
    path = tmp_path / project_key_for_directory("/w") / "g.jsonl"
    path.parent.mkdir(parents=True)
    write(path, [{"type": "user", "message": {"content": "one"}}])
    assert [m.message["content"] for m in transcripts.read_raw_transcript("g", "/w", tmp_path)] == ["one"]
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"type": "user", "message": {"content": "two"}}) + "\n")
        f.write('{"type": "user", "message": {"content": "thr')  # still being written
    assert [m.message["content"] for m in transcripts.read_raw_transcript("g", "/w", tmp_path)] == ["one", "two"]
    with path.open("a", encoding="utf-8") as f:
        f.write('ee"}}\n')
    assert [m.message["content"] for m in transcripts.read_raw_transcript("g", "/w", tmp_path)] == [
        "one",
        "two",
        "three",
    ]


def test_what_a_person_reads_skips_empty_prompts_and_assistant_lines_that_are_not_blocks():
    msgs = [
        SimpleNamespace(type="user", message={"content": "   "}),
        SimpleNamespace(type="user", message={"content": None}),
        SimpleNamespace(type="user", message={"content": [{"type": "text", "text": "a"}, {"type": "text"}]}),
        SimpleNamespace(type="assistant", message={"content": "plain words are not blocks"}),
        SimpleNamespace(type="system", message={"content": "not a turn"}),
        SimpleNamespace(type="assistant", message={"content": [{"type": "tool_use"}, {"type": "text", "text": " "}]}),
        SimpleNamespace(type="assistant", message={"content": [{"type": "text", "text": "done"}]}),
    ]
    assert transcripts.recent_turns(msgs, None) == [
        {"role": "user", "text": "a"},
        {"role": "assistant", "text": "done", "tools": ["?"]},
    ]


def asks(id_, question):
    tool = {"type": "tool_use", "id": id_, "name": "AskUserQuestion", "input": {"questions": [question]}}
    return json.dumps({"type": "assistant", "message": {"content": [tool]}})


def answers(id_):
    return json.dumps({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": id_}]}})


def test_the_open_question_is_the_newest_unanswered_one_and_odd_lines_are_skipped(tmp_path):
    path = tmp_path / project_key_for_directory("/w") / "q.jsonl"
    path.parent.mkdir(parents=True)

    def add(*lines):
        with path.open("a") as f:
            f.write("".join(line + "\n" for line in lines))

    bare = json.dumps({"type": "assistant", "message": {"content": ["a bare string"]}})
    add(asks("q1", {"question": "First?"}), "not json at all", bare, json.dumps({"type": "user"}), "[1, 2]")
    assert transcripts.open_question("q", "/w", tmp_path) == {"text": "First?", "options": []}
    add(asks("q2", {"question": "Second?"}), asks("q3", {"question": "Third?"}), answers("q3"))
    assert transcripts.open_question("q", "/w", tmp_path) is None  # it went past the second

    add(asks("q5", {"question": "Fifth?", "options": [{"label": "Ja"}, 3]}))
    assert transcripts.open_question("q", "/w", tmp_path) == {"text": "Fifth?", "options": ["Ja"]}
    add(answers("q1"))  # an answer to another question leaves this one open
    assert transcripts.open_question("q", "/w", tmp_path) == {"text": "Fifth?", "options": ["Ja"]}
    add(asks("q4", "not a dict"))
    assert transcripts.open_question("q", "/w", tmp_path) == {"text": "", "options": []}  # q4 asks nothing
    add(answers("q4"))
    assert transcripts.open_question("q", "/w", tmp_path) is None
    assert transcripts.open_question("missing", "/w", tmp_path) is None


def uses(id_, name="Bash"):
    tool = {"type": "tool_use", "id": id_, "name": name, "input": {}}
    return json.dumps({"type": "assistant", "message": {"content": [tool]}})


def transcript_of(tmp_path, *lines):
    path = tmp_path / project_key_for_directory("/w") / "q.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(line + "\n" for line in lines))


def test_a_question_the_session_went_past_is_not_open(tmp_path):
    interrupted = json.dumps({"type": "user", "message": {"content": "[Request interrupted by user]"}})
    typed = json.dumps({"type": "user", "message": {"content": [{"type": "text", "text": "do the other thing"}]}})
    reminder = json.dumps({"type": "user", "isMeta": True, "message": {"content": "a note from a hook"}})
    for later in (uses("t1"), interrupted, typed):
        transcript_of(tmp_path, asks("q1", {"question": "Old?"}), later)
        assert transcripts.open_question("q", "/w", tmp_path) is None, later
    transcript_of(tmp_path, asks("q1", {"question": "Still open?"}), reminder)
    assert transcripts.open_question("q", "/w", tmp_path) == {"text": "Still open?", "options": []}


def test_a_question_asked_inside_a_subagent_is_not_open(tmp_path):
    side = json.loads(asks("q2", {"question": "Asked inside a subagent?"}))
    side["isSidechain"] = True
    tool = json.loads(uses("t9"))
    tool["isSidechain"] = True
    transcript_of(tmp_path, asks("q1", {"question": "Mine?"}), json.dumps(side), json.dumps(tool))
    assert transcripts.open_question("q", "/w", tmp_path) == {"text": "Mine?", "options": []}


def test_a_session_waiting_on_a_permission_prompt_has_no_open_question(tmp_path):
    transcript_of(tmp_path, asks("q1", {"question": "Open?"}))
    assert transcripts.open_question("q", "/w", tmp_path, waiting_for="input needed") is not None
    assert transcripts.open_question("q", "/w", tmp_path, waiting_for="permission prompt") is None
