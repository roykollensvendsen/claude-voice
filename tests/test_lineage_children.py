"""Which children the bridge started, and when a process started, as the kernel says."""

import subprocess
import sys

import pytest

from claude_voice import lineage

linux = pytest.mark.skipif(not sys.platform.startswith("linux"), reason="read from /proc")


@pytest.fixture
def child(tmp_path):
    """A real process this test started, working in a folder of its own."""
    folder = tmp_path / "helper"
    folder.mkdir()
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], cwd=folder)
    yield proc, folder
    proc.kill()
    proc.wait()


@linux
def test_start_ticks_are_the_kernels_start_time_of_the_process(child):
    proc, _ = child
    stat = open(f"/proc/{proc.pid}/stat", encoding="ascii").read()
    assert lineage.start_ticks(proc.pid) == stat[stat.rindex(")") + 2 :].split()[19]


def test_a_process_that_is_gone_has_no_start_ticks(child):
    proc, _ = child
    proc.kill()
    proc.wait()
    assert lineage.start_ticks(proc.pid) is None


@linux
def test_the_children_working_in_a_folder_are_found_by_the_folder(child, tmp_path):
    proc, folder = child
    assert lineage.children_in(str(folder)) == [proc.pid]
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    assert lineage.children_in(str(elsewhere)) == []
