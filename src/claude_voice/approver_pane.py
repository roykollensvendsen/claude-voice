"""The approver's window (approver-v1, v1.5 and v1.7).

The bridge opens it as a new tmux session whose only command is the fixed starter, run
directly with no shell around it. tmux answers with the pid of the window's own process, and
that answer, not any file a session can write, is what the bridge pins, together with when
that process started. The pin holds only once the process has become claude by the path the
bridge resolved; a shell, a wrapper or anything else in its place is refused, never followed.
"""

from __future__ import annotations

import asyncio
import os
import time

from . import lineage

SETTLE = 0.1  # how often to look again while the starter becomes claude


async def open_window(tmux: list[str], name: str, cwd: str, argv: list[str]) -> int:
    """Open the window running `argv` as its own process, and return that process's pid."""
    made = await asyncio.create_subprocess_exec(
        *tmux,
        "new-session",
        "-d",
        "-P",
        "-F",
        "#{pane_pid}",
        "-s",
        name,
        "-c",
        cwd,
        "--",
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await made.communicate()
    if made.returncode != 0:
        raise RuntimeError(f"tmux could not open the approver's window: {err.decode(errors='replace').strip()}")
    return int(out.decode().strip())


def _is(pid: int, claude: str, ticks: str | None) -> bool:
    # RULE: the approvers window counts only while its own process is claude
    if ticks is None or lineage.start_ticks(pid) != ticks:
        return False
    try:
        return os.path.realpath(f"/proc/{pid}/exe") == os.path.realpath(claude)
    except OSError:
        return False


async def became(pid: int, claude: str, ticks: str | None, wait: float) -> bool:
    """True once the pinned process, still the same one, runs `claude`; False if it never does."""
    end = time.monotonic() + wait
    while True:
        if _is(pid, claude, ticks):
            return True
        if time.monotonic() >= end:
            return False
        await asyncio.sleep(SETTLE)
