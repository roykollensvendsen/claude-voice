"""Processes: whether one still runs, where a tool is installed, and who is on a socket.

Whether a process is still running is asked of the operating system without touching it.

os.kill(pid, 0) is the usual probe on Linux and macOS, but on Windows it ends the
process, so every liveness check in the bridge goes through here instead.

Which process sent something over a Unix socket, and whether it runs under another one, is
read from the kernel. Where the system cannot say (Windows, or no /proc), the answer is no.
"""

from __future__ import annotations

import os
import shutil
import socket
import struct
from pathlib import Path
from typing import Any

import psutil

#: How deep a process tree is walked before giving up; real trees are far shallower.
MAX_DEPTH = 64

# Where tools install themselves for one user. A systemd user service's search path
# leaves these out, so a tool there looked missing although the owner had it.
USER_BINS = ("~/.local/bin", "~/.cargo/bin", "~/.bun/bin", "~/bin", "/usr/local/bin")


def alive(pid: int) -> bool:
    """True while a process with this pid exists."""
    return psutil.pid_exists(pid)


def find(name: str) -> str | None:
    """Where tool `name` is: on the search path, or in one of the user's own tool folders."""
    extra = os.pathsep.join(str(Path(p).expanduser()) for p in USER_BINS)
    return shutil.which(name, path=os.pathsep.join(filter(None, [os.environ.get("PATH", ""), extra])))


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
