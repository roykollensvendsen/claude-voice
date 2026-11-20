"""Run a command and stop it on CPU time, never on how long a busy machine took.

The bridge's update runs the test suite before it switches over. A wall-clock limit failed
that run on a crowded machine, and the update rolled back with nothing wrong in the code.
Here a run is stopped for one of two reasons only:

- it used more CPU than a cap, which a loop that spins reaches;
- it used almost no CPU for ten minutes, which a deadlock does. A deadlock burns no CPU,
  so no CPU cap would ever stop it. A busy machine keeps a working run using CPU, so this
  rule cannot fire on load alone.

CPU comes from /proc. Where there is none, the run falls back to a limit on the clock and
says so. Design: update-tests-cpu-stop, agreed with voice-architect on 20.11.
"""

from __future__ import annotations

import os
import signal
import subprocess
import tempfile
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

IDLE = 600.0  # a run that used under LEAST CPU-seconds in this long has hung
LEAST = 1.0
WALL = 900.0  # the clock's limit, where CPU time cannot be read
GRACE = 10.0  # between asking a stopped run to end and making it
_FIELDS = 15  # fields after the name, up to and including cstime


def proc_stats() -> list[str] | None:
    """Every process's /proc/<pid>/stat line, or None where there is no /proc."""
    proc = Path("/proc")
    if not (proc / "self" / "stat").exists():
        return None
    lines = []
    for entry in proc.iterdir():
        if entry.name.isdigit():
            try:
                lines.append((entry / "stat").read_text(encoding="utf-8", errors="replace"))
            except OSError:
                continue  # it ended between the listing and the read
    return lines


def _tick() -> int:
    try:
        return os.sysconf("SC_CLK_TCK")
    except (AttributeError, ValueError, OSError):
        return 100


def group_cpu(
    pgid: int, stats: Callable[[], Iterable[str] | None] = proc_stats, tick: int | None = None
) -> float | None:
    """CPU seconds used so far by a process group, or None where it cannot be read.

    Each process in the group counts its own time and the time of the children it waited
    for, so a worker that has already exited still counts. Members are found by their group,
    not by following parents, so a child that forked twice still counts.
    """
    lines = stats()
    if lines is None:
        return None
    ticks = 0
    for line in lines:
        # The name sits in brackets and may hold spaces or brackets of its own.
        rest = line[line.rfind(")") + 2 :].split()
        if len(rest) < _FIELDS or rest[2] != str(pgid):
            continue
        ticks += sum(int(field) for field in rest[11:15])
    return ticks / (tick or _tick())


class Watch:
    """Decides, from a run's CPU seconds over time, whether to stop it and why."""

    def __init__(self, cap: float | None, idle: float = IDLE, least: float = LEAST, what: str = "the tests") -> None:
        self.cap, self.idle, self.least, self.what = cap, idle, least, what
        self._mark: tuple[float, float] | None = None  # (cpu, when) at the last sign of work

    def see(self, cpu: float, now: float) -> str | None:
        """Why the run should stop now, or None while it may go on."""
        # RULE: a test run that spins is stopped at its CPU cap
        if self.cap is not None and cpu > self.cap:
            return f"stopped: {self.what} used {cpu:.0f} CPU-seconds, over the cap of {self.cap:.0f}"
        if self._mark is None or cpu - self._mark[0] >= self.least:
            self._mark = (cpu, now)
            return None
        # RULE: a test run that uses almost no CPU for ten minutes has hung and is stopped
        if now - self._mark[1] >= self.idle:
            minutes = self.idle / 60
            return (
                f"stopped: {self.what} used under {self.least:g} CPU-second in {minutes:.0f} min, so the run had hung"
            )
        return None


@dataclass(frozen=True)
class Finished:
    """How a run ended: its exit code (None when stopped), all it printed, and why it was stopped."""

    code: int | None
    output: str
    note: str


def _stop(child: subprocess.Popen[bytes]) -> None:
    """End the run's whole group: ask first, then make it. Never an interrupt, which uv passes on twice."""
    killpg = getattr(os, "killpg", None)
    try:
        if killpg is not None:
            killpg(child.pid, signal.SIGTERM)
        else:
            child.terminate()
        child.wait(timeout=GRACE)
    except subprocess.TimeoutExpired:
        if killpg is not None:
            killpg(child.pid, getattr(signal, "SIGKILL", signal.SIGTERM))
        else:
            child.kill()
        child.wait()
    except ProcessLookupError:
        child.wait()


def run(
    command: list[str],
    cwd: Path | None,
    *,
    cap: float | None,
    poll: float = 5.0,
    clock: Callable[[], float] = time.monotonic,
    cpu: Callable[[int], float | None] = group_cpu,
    what: str = "the tests",
) -> Finished:
    """Run `command` in a group of its own, with stdout and stderr together, until it ends or is stopped."""
    with tempfile.TemporaryFile() as out:
        child = subprocess.Popen(command, cwd=cwd, stdout=out, stderr=subprocess.STDOUT, start_new_session=True)
        watch, started, note = Watch(cap, what=what), clock(), ""
        while True:
            try:
                child.wait(timeout=poll)
                break
            except subprocess.TimeoutExpired:
                pass
            used, now = cpu(child.pid), clock()
            if used is None:
                # RULE: where CPU time cannot be read the test run falls back to the clock and says so
                over = now - started > WALL
                why = (
                    f"stopped: CPU time cannot be read here, so {what} had {WALL:.0f} s on the clock" if over else None
                )
            else:
                why = watch.see(used, now)
            if why:
                _stop(child)
                note = why[0].upper() + why[1:] + "."
                break
        out.seek(0)
        output = out.read().decode(errors="replace")
    return Finished(None if note else child.returncode, output, note)
