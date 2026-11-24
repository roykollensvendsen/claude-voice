"""Where Claude Code keeps a session's transcript, found in one place for the whole bridge."""

import re
from pathlib import Path

from claude_agent_sdk import project_key_for_directory

from claude_voice import transcript_files

SOURCE = Path(transcript_files.__file__).parent


def test_a_transcript_is_the_session_file_in_its_working_folders_project_folder(tmp_path):
    cwd = "/home/someone/project"
    folder = tmp_path / project_key_for_directory(cwd)
    assert transcript_files.project_folder(tmp_path, cwd) == folder
    assert transcript_files.transcript_path(tmp_path, cwd, "sid-1") == folder / "sid-1.jsonl"
    assert transcript_files.transcript_path(tmp_path, Path(cwd), "sid-1") == folder / "sid-1.jsonl"


def test_only_one_module_works_out_where_claude_code_keeps_a_transcript():
    """A later change to Claude Code's layout is then made once, not in ten places."""
    spelled = re.compile(r"project_key_for_directory|\.claude/projects")
    found = sorted(
        p.relative_to(SOURCE).as_posix() for p in SOURCE.rglob("*.py") if spelled.search(p.read_text(encoding="utf-8"))
    )
    assert found == ["transcript_files.py"]
