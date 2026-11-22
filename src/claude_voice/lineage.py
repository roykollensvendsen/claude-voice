"""Lineage: which process is on the other end of a socket, whose child it is, and which
children the bridge itself started.

Which process sent something over a Unix socket, and whether it runs under another one, is
read from the kernel. Where the system cannot say (Windows, or no /proc), the answer is no.
Every session runs as the same Unix user, so this guards against slips and simple stand-ins.
It is not a wall between users: one user can still look inside its own processes.
"""

from __future__ import annotations

import contextlib
import socket
import struct
from pathlib import Path
from typing import Any

import psutil

#: How deep a process tree is walked before giving up; real trees are far shallower.
MAX_DEPTH = 64


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


def descends(pid: int, ancestor: int, started: str | None) -> bool:
    """Whether process `pid` is `ancestor` or runs under it, where `ancestor` is still the
    process that started at `started` ticks."""
    # RULE: a dead or reused approver pid proves nothing
    if started is None or start_ticks(ancestor) != started:
        return False
    for _ in range(MAX_DEPTH):
        if pid == ancestor:
            return True
        fields = _stat(pid)
        if fields is None:
            return False
        pid = int(fields[1])  # the parent
    return False


def peer_pid(sock: Any) -> int | None:
    """The pid of the process on the other end of a Unix socket, as the kernel names it,
    or None where the system cannot say."""
    option = getattr(socket, "SO_PEERCRED", None)
    # RULE: without the kernels word on the sender no report is taken
    if option is None or sock is None:
        return None
    pid, _uid, _gid = struct.unpack("3i", sock.getsockopt(socket.SOL_SOCKET, option, struct.calcsize("3i")))
    return pid or None
