"""The update's test run stops on CPU time, never on a busy machine's clock.

Design: update-tests-cpu-stop, agreed with voice-architect on 20.11.
"""

from __future__ import annotations

import subprocess
import sys
import time

import pytest

from claude_voice import cpu_watch

TICK = 100  # clock ticks per second in the made-up /proc lines


def stat(pid, pgrp, utime=0, stime=0, cutime=0, cstime=0, comm="python3"):
    """One /proc/<pid>/stat line, with only the fields the watch reads filled in."""
    rest = ["S", "1", str(pgrp), *["0"] * 8, str(utime), str(stime), str(cutime), str(cstime), *["0"] * 5]
    return f"{pid} ({comm}) " + " ".join(rest)


def test_the_cpu_of_every_process_in_the_group_is_counted_and_others_are_not():
    lines = [stat(10, 10, utime=150, stime=50), stat(11, 10, utime=100), stat(12, 99, utime=5000)]
    assert cpu_watch.group_cpu(10, lambda: lines, tick=TICK) == pytest.approx(3.0)


def test_the_cpu_of_workers_that_have_already_exited_is_counted():
    """A finished worker's time lives on in the cutime and cstime of whoever waited for it."""
    lines = [stat(10, 10, utime=100, cutime=300, cstime=100)]
    assert cpu_watch.group_cpu(10, lambda: lines, tick=TICK) == pytest.approx(5.0)


def test_a_name_with_spaces_and_brackets_does_not_shift_the_fields():
    lines = [stat(10, 10, utime=200, comm="a (b) c")]
    assert cpu_watch.group_cpu(10, lambda: lines, tick=TICK) == pytest.approx(2.0)


def test_without_proc_the_cpu_cannot_be_read():
    assert cpu_watch.group_cpu(10, lambda: None, tick=TICK) is None


def test_a_test_run_that_spins_is_stopped_at_its_cpu_cap():
    watch = cpu_watch.Watch(cap=540.0)
    assert watch.see(cpu=539.0, now=100.0) is None
    said = watch.see(cpu=541.0, now=105.0)
    assert said is not None and "541 CPU-seconds" in said and "540" in said


def test_a_test_run_that_uses_almost_no_cpu_for_ten_minutes_has_hung_and_is_stopped():
    watch = cpu_watch.Watch(cap=540.0)
    assert watch.see(cpu=12.0, now=0.0) is None
    assert watch.see(cpu=12.5, now=9 * 60.0) is None
    said = watch.see(cpu=12.9, now=10 * 60.0)
    assert said is not None and "under 1 CPU-second in 10 min" in said


def test_a_busy_but_slow_tree_is_stopped_by_neither_rule():
    """Half a CPU-second a minute, for two hours: a crowded machine, still working."""
    watch = cpu_watch.Watch(cap=540.0)
    assert all(watch.see(cpu=minute * 0.5, now=minute * 60.0) is None for minute in range(121))


def test_where_cpu_time_cannot_be_read_the_test_run_falls_back_to_the_clock_and_says_so():
    clock = iter([0.0, 5.0, 901.0, 901.0, 901.0])
    done = cpu_watch.run(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        cwd=None,
        cap=540.0,
        poll=0.01,
        clock=lambda: next(clock),
        cpu=lambda pgid: None,
    )
    assert done.code is None
    assert "CPU time cannot be read here" in done.note
    assert "900 s" in done.note


def test_a_stopped_run_is_ended_and_says_which_rule_stopped_it():
    seen = iter([1.0, 600.0])
    done = cpu_watch.run(
        [sys.executable, "-c", "print('started', flush=True); import time; time.sleep(60)"],
        cwd=None,
        cap=540.0,
        poll=0.05,
        clock=lambda: 0.0,
        cpu=lambda pgid: next(seen, 600.0),
    )
    assert done.code is None
    assert "600 CPU-seconds" in done.note
    assert "started" in done.output


def test_a_run_that_finishes_gives_its_exit_code_and_all_it_printed():
    done = cpu_watch.run(
        [sys.executable, "-c", "import sys; print('out'); print('err', file=sys.stderr); sys.exit(3)"],
        cwd=None,
        cap=540.0,
        poll=0.01,
    )
    assert done.code == 3
    assert "out" in done.output and "err" in done.output
    assert done.note == ""


@pytest.mark.skipif(sys.platform == "win32", reason="process groups")
def test_a_stop_ends_the_whole_group_with_term_and_never_with_interrupt(monkeypatch):
    """uv passes an interrupt on to pytest, so pytest would get it twice; a group TERM reaches each once."""
    sent = []
    real = cpu_watch.os.killpg

    def killpg(pgid, sig):
        sent.append(sig)
        real(pgid, sig)

    monkeypatch.setattr(cpu_watch.os, "killpg", killpg)
    cpu_watch.run(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        cwd=None,
        cap=540.0,
        poll=0.01,
        cpu=lambda pgid: 999.0,
    )
    assert sent[0] == cpu_watch.signal.SIGTERM
    assert cpu_watch.signal.SIGINT not in sent


def test_the_real_reader_sees_the_cpu_of_a_running_group():
    """On this machine, /proc gives a group's own CPU: a child that burns 0.3 s shows it."""
    if cpu_watch.proc_stats() is None:
        pytest.skip("no /proc here")
    child = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import time\nend = time.process_time() + 0.3\nwhile time.process_time() < end: pass\ninput()",
        ],
        stdin=subprocess.PIPE,
        start_new_session=True,
    )
    try:
        used = 0.0
        for _ in range(200):
            used = cpu_watch.group_cpu(child.pid) or 0.0
            if used >= 0.25:  # noqa: PLR2004 - most of the 0.3 s burnt
                break
            time.sleep(0.05)
        assert used >= 0.25  # noqa: PLR2004
    finally:
        child.kill()
        child.wait()
