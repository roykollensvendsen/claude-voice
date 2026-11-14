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
