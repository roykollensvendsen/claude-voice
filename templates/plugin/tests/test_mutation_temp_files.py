"""A mutation run leaves no temporary files behind, even when it is stopped part way.

`scripts/mutate.py` runs the suite once per rule, and every suite makes its own temporary
files. Over a day of runs those once filled a machine's temporary space. Each suite now keeps
them in a folder of its own inside the run's, removed when it ends, and the run's own folder
goes when the run does. Whatever a killed run left is swept at the next start
(`scripts/temproot.py`, proven in `tests/test_temproot.py`).
"""

import importlib.util
import os
import pathlib
import shutil
import signal
import subprocess
import sys
import time

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


def test_a_suite_s_own_processes_keep_their_files_in_its_folder(tmp_path, monkeypatch):
    mutate = _mutate(tmp_path, monkeypatch)
    seen = tmp_path / "seen.txt"
    (mutate.ROOT / "tests" / "test_where.py").write_text(
        "import tempfile, pathlib\n"
        f"def test_where():\n    pathlib.Path({str(seen)!r}).write_text(tempfile.gettempdir())\n"
    )
    mutate.run_suite(["tests/test_where.py"], tmp_path / "run")
    assert pathlib.Path(seen.read_text()).parent == tmp_path / "run"


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


def test_the_temporary_files_live_outside_the_checkout():
    """Inside it, a test that makes a git repository of its own would find this one instead."""
    spec = importlib.util.spec_from_file_location("mutate_where", ROOT / "scripts/mutate.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert ROOT.resolve() not in module.MUTATE_TMP.resolve().parents


SLOW_TEST = "import time\n\ndef test_a_slow_rule():\n    time.sleep(60)\n"
ROW = '[[rule]]\nname = "a slow rule"\nfile = "src/pkg/core.py"\nfind = "^X = 1$"\nreplace = "X = 2"\n'


def test_a_mutation_run_stopped_by_sigterm_restores_the_file_and_leaves_nothing(tmp_path):
    """CI cancels a job with SIGTERM: the switched-off line comes back, and every folder goes."""
    project = tmp_path / "project"
    for folder in ("scripts/mutations/src/pkg/core.py", "src/pkg", "tests"):
        (project / folder).mkdir(parents=True)
    for name in ("mutate.py", "temproot.py"):
        shutil.copy(ROOT / "scripts" / name, project / "scripts" / name)
    source = project / "src/pkg/core.py"
    source.write_text("# RULE: a slow rule\nX = 1\n")
    (project / "scripts/mutations/src/pkg/core.py/a-slow-rule.toml").write_text(ROW)
    (project / "tests/test_core.py").write_text(SLOW_TEST)
    base = tmp_path / "tmp"
    base.mkdir()
    run = subprocess.Popen(
        [sys.executable, "scripts/mutate.py"],
        cwd=project,
        env={**os.environ, "TMPDIR": str(base)},
        stdout=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 30
        while not list(base.glob("gizmo-mcp-mutate-*/run-*/suite-*")) and time.monotonic() < deadline:
            time.sleep(0.1)
        assert source.read_text().endswith("X = 2\n"), "the run never switched the rule off"
        run.send_signal(signal.SIGTERM)
        assert run.wait(timeout=30) == 143
    finally:
        run.kill()
    assert source.read_text() == "# RULE: a slow rule\nX = 1\n"
    assert not (project / "scripts/.mutate-recovery.json").exists()
    assert list(base.iterdir()) == []
