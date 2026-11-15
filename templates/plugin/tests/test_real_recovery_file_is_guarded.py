"""A test run must leave the rule check's real recovery file as it found it.

`scripts/mutate.py` keeps a file it is mutating in `scripts/.mutate-recovery.json`
while the suite runs. A test that called the runner without pointing `RECOVERY`
at its own temporary file would write or remove that real file, and in the middle
of a mutation run that loses the original. `tests/conftest.py` fails the whole run
when it happens. These tests copy it to a scratch project and prove it there.
"""

import os
import pathlib
import shutil
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).parent.parent

WRITES = "def test_it(): (__import__('pathlib').Path('scripts/.mutate-recovery.json')).write_text('{}')\n"
REMOVES = "def test_it(): (__import__('pathlib').Path('scripts/.mutate-recovery.json')).unlink()\n"
LEAVES = "def test_it(): pass\n"


def _run(tmp_path, test, *, there_before):
    """Run `test` in a scratch project guarded by this repository's conftest."""
    (tmp_path / "tests").mkdir()
    (tmp_path / "scripts").mkdir()
    (tmp_path / "tests/conftest.py").write_text((ROOT / "tests/conftest.py").read_text(encoding="utf-8"))
    shutil.copy(ROOT / "scripts/temproot.py", tmp_path / "scripts/temproot.py")
    (tmp_path / "tmp").mkdir()
    (tmp_path / "tests/test_scratch.py").write_text(test)
    if there_before:
        (tmp_path / "scripts/.mutate-recovery.json").write_text('{"file": "kept"}')
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--rootdir", str(tmp_path), "tests"],
        cwd=tmp_path,
        # A temporary folder of its own: a run that fails keeps pytest's files there, for reading.
        env={**os.environ, "TMPDIR": str(tmp_path / "tmp")},
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize(
    ("test", "there_before"),
    [(WRITES, False), (REMOVES, True), (WRITES, True)],
    ids=["written", "removed", "overwritten"],
)
def test_a_run_that_changes_the_real_recovery_file_fails(tmp_path, test, there_before):
    done = _run(tmp_path, test, there_before=there_before)
    assert done.returncode != 0, done.stdout
    assert "scripts/.mutate-recovery.json" in done.stdout


@pytest.mark.parametrize("there_before", [False, True], ids=["absent", "there all along"])
def test_a_run_that_leaves_it_alone_passes(tmp_path, there_before):
    """During a mutation run the file is there all along; the suite it starts must still pass."""
    done = _run(tmp_path, LEAVES, there_before=there_before)
    assert done.returncode == 0, done.stdout
