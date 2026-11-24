"""The check for a rule a stopped mutation run left switched off (claude-voice #514, #515)."""

import pathlib

from voice_checks import cli
from voice_checks.left_behind import scan

ROW = "[[rule]]\nname = {name!r}\nfile = {file!r}\nfind = 'if ready:'\nreplace = 'if False:'\n"


def repo(tmp_path: pathlib.Path, source: str, table: bool = False) -> pathlib.Path:
    (tmp_path / "src").mkdir()
    (tmp_path / "src/door.py").write_text(source, encoding="utf-8")
    row = ROW.format(name="a press needs a yes", file="src/door.py")
    if table:
        (tmp_path / "scripts").mkdir()
        (tmp_path / "scripts/mutations.toml").write_text(row, encoding="utf-8")
    else:
        folder = tmp_path / "scripts/mutations/src/door.py"
        folder.mkdir(parents=True)
        (folder / "a-press-needs-a-yes.toml").write_text(row, encoding="utf-8")
    return tmp_path


LEFT = "def press(ready):\n    # RULE: a press needs a yes\n    if False:\n        return 'pressed'\n"
BACK = "def press(ready):\n    # RULE: a press needs a yes\n    if ready:\n        return 'pressed'\n"


def test_a_rule_left_switched_off_is_named_with_its_file_and_line(tmp_path):
    assert scan(repo(tmp_path, LEFT)) == [
        "src/door.py:3: 'a press needs a yes' is left switched off; put its own line back"
    ]


def test_a_rule_whose_line_is_in_place_passes(tmp_path):
    assert scan(repo(tmp_path, BACK)) == []


def test_the_replacement_on_its_own_is_no_leftover(tmp_path):
    """`if False:` may stand on purpose, even beside the rule; only the rule's own line gone counts."""
    assert scan(repo(tmp_path, BACK + "    if False:\n        pass\n")) == []


def test_a_row_in_the_shared_table_is_read_too(tmp_path):
    assert len(scan(repo(tmp_path, LEFT, table=True))) == 1


def test_a_repository_with_no_mutation_rows_passes(tmp_path):
    assert scan(tmp_path) == []


def test_a_row_whose_file_is_gone_is_named(tmp_path):
    root = repo(tmp_path, BACK)
    (root / "src/door.py").unlink()
    assert scan(root) == ["src/door.py: the row for 'a press needs a yes' names a file that is not there"]


def test_the_command_fails_and_names_each_leftover(tmp_path, capsys):
    assert cli.main(["left-behind", "--root", str(repo(tmp_path, LEFT))]) == 1
    assert "src/door.py:3:" in capsys.readouterr().out


def test_the_command_passes_when_every_rule_is_in_place(tmp_path, capsys):
    assert cli.main(["left-behind", "--root", str(repo(tmp_path, BACK))]) == 0
    assert "switched off" in capsys.readouterr().out
