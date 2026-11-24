"""Where Claude Code keeps a session's transcript.

Claude Code writes each session's conversation to a file of its own, one line per turn. The
file sits in a folder named after the session's working folder, under the projects folder.
Every part of the bridge that reads one finds it here, so a change in that layout is made once.
"""

from __future__ import annotations

from pathlib import Path

from claude_agent_sdk import project_key_for_directory

#: Claude Code's projects folder: one folder per working folder, holding its sessions' transcripts.
PROJECTS_DIR = Path("~/.claude/projects").expanduser()


def project_folder(projects_dir: Path, cwd: str | Path) -> Path:
    """The folder holding the transcripts of every session that works in `cwd`."""
    return projects_dir / project_key_for_directory(cwd)


def transcript_path(projects_dir: Path, cwd: str | Path, session_id: str) -> Path:
    """The transcript of session `session_id`, which works in `cwd`; it may not exist yet."""
    return project_folder(projects_dir, cwd) / f"{session_id}.jsonl"
