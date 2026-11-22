"""No test writes under the owner's real home: the bridge's state folder, his settings, his Claude
Code files. A test once called the bridge's start under a systemd session and rewrote the bridge's
restart record in the owner's state folder. The suite now refuses such a write, and fails the test.
"""

import os
from pathlib import Path

import pytest
from conftest import REAL_HOME, WroteToTheOwnersHome

from claude_voice import config, restarts

# A folder that does not exist, so a write that got past the guard would fail, not land.
PROBE = REAL_HOME / ".claude-voice-guard-probe-that-never-exists" / "probe"


@pytest.mark.parametrize(
    "write",
    [
        lambda: PROBE.write_text("x", encoding="utf-8"),
        lambda: open(PROBE, "ab"),  # noqa: SIM115
        lambda: os.open(PROBE, os.O_WRONLY | os.O_CREAT),
        lambda: PROBE.unlink(),
        lambda: PROBE.parent.mkdir(),
        lambda: os.replace(PROBE, PROBE.with_name("other")),
    ],
)
def test_a_test_that_writes_under_the_owners_home_is_refused_and_fails(no_writes_to_the_owners_home, write):
    with pytest.raises(WroteToTheOwnersHome):
        write()
    assert no_writes_to_the_owners_home  # noted, so the test fails even had it swallowed the refusal
    no_writes_to_the_owners_home.clear()


def test_reading_the_owners_home_and_writing_in_the_checkout_or_temp_still_work(tmp_path):
    assert Path(__file__).read_text(encoding="utf-8")  # the checkout lives under the home here
    (tmp_path / "f").write_text("x")


def test_a_process_a_test_starts_has_a_home_of_its_own():
    """The hook cannot see another process, so the home it is handed must not be the owner's."""
    import subprocess
    import sys

    code = "import os, pathlib; print(pathlib.Path.home()); print(os.environ.get('XDG_STATE_HOME', ''))"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout
    for line in out.splitlines():
        assert not Path(line).resolve().is_relative_to(REAL_HOME) or Path(line).resolve().is_relative_to(
            Path(__file__).resolve().parents[1]
        )
    assert Path(out.splitlines()[0]).resolve() != REAL_HOME


def test_the_suite_is_never_the_bridges_own_service():
    assert "INVOCATION_ID" not in os.environ
    assert not config.STATE_DIR.resolve().is_relative_to(REAL_HOME / ".local")
    # Even read as the service's start, the restart record lands in the suite's own state folder.
    record = restarts.restart_info(config.STATE_DIR, environ={"INVOCATION_ID": "x"}, git=lambda *a: None)
    assert record["reason"] is not None
