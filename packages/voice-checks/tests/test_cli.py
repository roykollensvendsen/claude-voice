"""The command each plugin runs: what it says, and the exit status CI reads."""

from voice_checks.cli import main


def test_temp_files_fails_and_names_each_place(tmp_path, capsys):
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts/tool.py").write_text("import tempfile\nd = tempfile.mkdtemp()\n", encoding="utf-8")
    assert main(["temp-files", "--root", str(tmp_path)]) == 1
    assert "scripts/tool.py:2: mkdtemp" in capsys.readouterr().out


def test_temp_files_passes_a_clean_tree(tmp_path, capsys):
    (tmp_path / "src").mkdir()
    (tmp_path / "src/core.py").write_text("x = 1\n", encoding="utf-8")
    assert main(["temp-files", "--root", str(tmp_path)]) == 0
    assert "no temporary file" in capsys.readouterr().out
