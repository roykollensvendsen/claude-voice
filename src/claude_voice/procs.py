"""Processes: whether one still runs, and where a tool is installed.

Whether a process is still running is asked of the operating system without touching it.

os.kill(pid, 0) is the usual probe on Linux and macOS, but on Windows it ends the
process, so every liveness check in the bridge goes through here instead.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import psutil

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
