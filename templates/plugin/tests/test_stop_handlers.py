"""A test that installs stop handlers leaves none behind for the tests after it.

A test that calls a script's main() installs that script's handlers for SIGINT, SIGTERM and
SIGHUP in the pytest process itself. A later test that stops a run of its own would then meet
them, and fail for a reason that is not its own: the mutation check reports that as a rule
"killed by another test". `tests/conftest.py` puts the handlers back after each test.
"""

import os
import shutil
import subprocess
import sys

from conftest import ROOT

#: The first test changes all three handlers and never puts them back; the second checks them.
TWO_TESTS = """
import signal

STOPS = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)
BEFORE = {each: signal.getsignal(each) for each in STOPS}


def test_1_installs_handlers_and_leaves_them():
    for each in STOPS:
        signal.signal(each, lambda *_: None)


def test_2_finds_the_handlers_it_started_with():
    assert {each: signal.getsignal(each) for each in STOPS} == BEFORE
"""


def test_a_test_s_stop_handlers_are_gone_before_the_next_test(tmp_path):
    project = tmp_path / "project"
    (project / "tests").mkdir(parents=True)
    (project / "scripts").mkdir()
    shutil.copy(ROOT / "tests/conftest.py", project / "tests/conftest.py")
    shutil.copy(ROOT / "scripts/temproot.py", project / "scripts/temproot.py")
    (project / "tests/test_scratch.py").write_text(TWO_TESTS)
    base = tmp_path / "tmp"
    base.mkdir()
    env = {k: v for k, v in os.environ.items() if k != "GIZMO_MCP_TEST_ROOT"} | {"TMPDIR": str(base)}
    done = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--rootdir", str(project), "tests"],
        cwd=project,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stdout + done.stderr
    assert "2 passed" in done.stdout
