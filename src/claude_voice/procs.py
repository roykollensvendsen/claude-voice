"""Whether a process is still running, asked of the operating system without touching it.

os.kill(pid, 0) is the usual probe on Linux and macOS, but on Windows it ends the
process, so every liveness check in the bridge goes through here instead.
"""

from __future__ import annotations

import psutil


def alive(pid: int) -> bool:
    """True while a process with this pid exists."""
    return psutil.pid_exists(pid)
