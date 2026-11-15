"""A mutation run leaves no temporary files behind.

`scripts/mutate.py` runs the whole suite once per rule, and every run made its
own pytest folder of temporary files. Over a day of runs those folders filled
the machine's temporary space until no program could start a command. Each run
now gets a folder of its own, outside the checkout, removed when the run ends;
whatever a killed run left is swept away at the next start.
"""

import importlib.util
import os
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).parent.parent

LEAVES_FILES = """
import os

def test_it_writes_files_it_cannot_delete(tmp_path):
    (tmp_path / "plain.txt").write_text("x")
    (tmp_path / "locked.txt").write_text("x")
    os.chmod(tmp_path / "locked.txt", 0)
    (tmp_path / "shut").mkdir()
    (tmp_path / "shut" / "inside.txt").write_text("x")
    os.chmod(tmp_path / "shut", 0)
"""


def _mutate(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("mutate_temp", ROOT / "scripts/mutate.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    project = tmp_path / "project"
    (project / "tests").mkdir(parents=True)
    (project / "tests" / "test_leaves_files.py").write_text(LEAVES_FILES)
    monkeypatch.setattr(module, "ROOT", project)
    monkeypatch.setattr(module, "MUTATE_TMP", tmp_path / "mutate-tmp")
    return module


def test_a_suite_run_leaves_no_temporary_files_behind(tmp_path, monkeypatch):
    mutate = _mutate(tmp_path, monkeypatch)
    failed, _ = mutate.run_suite()
    assert failed == 0
    assert list(mutate.MUTATE_TMP.iterdir()) == []


@pytest.mark.skipif(sys.platform == "win32", reason="Windows needs extra rights to make a link, and has no 0755")
def test_removing_a_run_s_files_never_changes_a_folder_it_links_to(tmp_path, monkeypatch):
    mutate = _mutate(tmp_path, monkeypatch)
    outside = tmp_path / "outside"
    outside.mkdir()
    outside.chmod(0o755)
    (outside / "kept.txt").write_text("x")
    (mutate.ROOT / "tests" / "test_links_out.py").write_text(
        f"def test_it_links_out(tmp_path):\n    (tmp_path / 'link').symlink_to({str(outside)!r})\n"
    )
    failed, _ = mutate.run_suite()
    assert failed == 0
    assert (outside.stat().st_mode & 0o777, (outside / "kept.txt").exists()) == (0o755, True)


def test_a_killed_run_s_temporary_files_are_swept_at_the_next_start(tmp_path, monkeypatch):
    mutate = _mutate(tmp_path, monkeypatch)
    left = mutate.MUTATE_TMP / "run-left-by-a-killed-run"
    (left / "shut").mkdir(parents=True)
    (left / "shut" / "inside.txt").write_text("x")
    os.chmod(left / "shut", 0)
    mutate.sweep()
    assert not left.exists()


def test_the_temporary_files_live_outside_the_checkout():
    """Inside it, a test that makes a git repository of its own would find this one instead."""
    spec = importlib.util.spec_from_file_location("mutate_where", ROOT / "scripts/mutate.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert ROOT.resolve() not in module.MUTATE_TMP.resolve().parents


def test_a_passing_test_keeps_no_temporary_files():
    settings = (ROOT / "pyproject.toml").read_text()
    assert 'tmp_path_retention_policy = "failed"' in settings
    assert "tmp_path_retention_count = 1" in settings


SLOW = "import time\n\ndef test_it_takes_a_while():\n    time.sleep(60)\n"

STOPPED_RUN = """
import importlib.util, pathlib, sys
spec = importlib.util.spec_from_file_location("mutate_stopped", sys.argv[1])
mutate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mutate)
mutate.ROOT = pathlib.Path(sys.argv[2])
mutate.MUTATE_TMP = pathlib.Path(sys.argv[3])
mutate.stop_cleanly()
mutate.run_suite()
"""


@pytest.mark.skipif(sys.platform == "win32", reason="Windows has no polite stop signal to send")
def test_a_stopped_mutation_run_still_cleans_up(tmp_path):
    """A run stopped politely, as CI cancels one or a terminal closes, removes its folder too.

    Python runs no `finally` on a stop signal unless it is told to, so a cancelled run used to
    leave its whole folder of temporary files behind for the next start to find.
    """
    import signal
    import subprocess
    import time

    project, scratch = tmp_path / "project", tmp_path / "mutate-tmp"
    (project / "tests").mkdir(parents=True)
    (project / "tests" / "test_slow.py").write_text(SLOW)
    script = tmp_path / "stopped_run.py"
    script.write_text(STOPPED_RUN)
    run = subprocess.Popen([sys.executable, str(script), str(ROOT / "scripts/mutate.py"), str(project), str(scratch)])
    try:
        deadline = time.monotonic() + 60
        while not (scratch.is_dir() and any(scratch.iterdir())):
            assert time.monotonic() < deadline, "the run never made its folder"
            time.sleep(0.1)
        run.send_signal(signal.SIGTERM)
        assert run.wait(60) == 128 + signal.SIGTERM
    finally:
        run.kill()
    assert list(scratch.iterdir()) == []


def test_the_mutation_run_turns_a_stop_into_an_exit_before_it_starts(tmp_path, monkeypatch):
    mutate = _mutate(tmp_path, monkeypatch)
    seen = []
    monkeypatch.setattr(mutate, "stop_cleanly", lambda: seen.append("stop"))
    monkeypatch.setattr(mutate, "all_rows", lambda: [])
    monkeypatch.setattr(mutate, "ROWS", mutate.ROOT / "scripts/mutations")
    monkeypatch.setattr(sys, "argv", ["mutate.py"])
    mutate.main()
    assert seen == ["stop"]
