"""Lineage: when a process started, and which children the bridge itself started.

Read from the kernel; where the system cannot say (Windows, or no /proc), the answer is none.
"""

from __future__ import annotations

import contextlib
from pathlib import Path

import psutil


def _stat(pid: int) -> list[str] | None:
    """A process's /proc stat fields from its state (field 3) on, or None when it is gone."""
    try:
        text = Path(f"/proc/{pid}/stat").read_text(encoding="ascii", errors="replace")
    except OSError:
        return None
    return text[text.rindex(")") + 2 :].split()  # the name in brackets may hold spaces


def start_ticks(pid: int) -> str | None:
    """When a process started, in clock ticks after boot (stat field 22), as Claude Code's
    live file writes it in procStart. A pid used again by a new process has another one."""
    fields = _stat(pid)
    return fields[19] if fields else None


def children_in(folder: str) -> list[int]:
    """The pids of this process's own children that work in `folder`."""
    want = Path(folder).resolve()
    found = []
    for child in psutil.Process().children():
        with contextlib.suppress(psutil.Error, OSError):
            if Path(child.cwd()).resolve() == want:
                found.append(child.pid)
    return found
