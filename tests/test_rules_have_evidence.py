"""Every rule this project enforces has evidence, and the evidence names it.

A rule here is one line of source marked ``# RULE: what it says``. Three things
have to be true of each, and none of them is true by writing the rule:

* it has a row, in ``scripts/mutations.toml`` or in a file of its own under
  ``scripts/mutations/``, so something switches it off;
* a test carries the rule's own words as its name;
* switching it off turns *that* test red, which ``scripts/mutate.py`` checks.

The second is what makes the third readable. A mutation breaks whatever it
breaks, and a kill only means something when the test that went red is the one
that names the rule — otherwise a documented example that happened to change can
leave a rule looking guarded while nothing guards it.
"""

import importlib.util
import pathlib
import re

ROOT = pathlib.Path(__file__).parent.parent
MARKER = re.compile(r"^[ \t]*#\s*RULE:\s*(.+?)\s*$")


def _mutate():
    """The mutation runner, loaded by path: scripts/ is not a package."""
    spec = importlib.util.spec_from_file_location("mutate", ROOT / "scripts/mutate.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def marked_rules() -> set[str]:
    """Every rule the source marks, by the words the marker gives it."""
    found = set()
    for source in sorted((ROOT / "src").rglob("*.py")):
        for line in source.read_text(encoding="utf-8").splitlines():
            match = MARKER.search(line)
            if match:
                found.add(match.group(1))
    return found


def rowed_rules() -> set[str]:
    """Every rule the mutation rows claim to switch off."""
    return {str(rule["name"]) for rule in _mutate().all_rows()}


def defined_test_names() -> set[str]:
    """Every test function this suite defines.

    Not called ``test_names``: pytest collects anything starting with ``test_``,
    and a helper that returns a set would be reported as a test that forgot to
    assert.
    """
    found = set()
    for module in sorted((ROOT / "tests").rglob("test_*.py")):
        found.update(re.findall(r"^(?:async )?def (test_\w+)", module.read_text(encoding="utf-8"), re.MULTILINE))
    return found


def test_every_rule_in_the_source_has_a_row_in_the_mutation_table():
    """Without this the evidence falls behind the code and nobody sees it happen."""
    marked, rowed = marked_rules(), rowed_rules()
    assert marked - rowed == set(), f"marked in the source and never switched off: {marked - rowed}"
    assert rowed - marked == set(), f"a row for a rule no longer in the source: {rowed - marked}"


def test_every_rule_has_a_test_named_after_it():
    """A kill is only evidence about a rule when the test that went red names it."""
    name_for = _mutate().test_name_for
    named = defined_test_names()
    missing = sorted(rule for rule in marked_rules() if name_for(rule) not in named)
    wanted = ", ".join(f"{name_for(rule)}()" for rule in missing)
    assert not missing, f"rules with no test named after them — write {wanted}"


def test_every_row_in_the_mutation_table_leaves_code_that_compiles():
    """The runner replaces the whole line, keeping only its indentation.

    A row written as if it swapped part of the line leaves a line cut off
    mid-expression: the module no longer imports, every test in the file errors,
    and the run reports the rule as killed by another test instead of naming the
    row. Checked here, the slip shows on any run of the suite.
    """
    mutate = _mutate()
    broken = []
    for rule in mutate.all_rows():
        text = (ROOT / rule["file"]).read_text(encoding="utf-8")
        why = mutate.broken(rule["file"], mutate.switched_off(text, rule))
        if why:
            broken.append(f"{rule['name']}: {why}")
    assert not broken, "rows whose replacement is not a whole line of valid code: " + "; ".join(broken)


def test_every_row_switches_off_the_line_beside_its_own_marker():
    """A row finds its line by pattern, the first match unless it names another.

    New code with a line like the rule's, placed above it in the same file, takes
    that first match. The run then switches off the new line, and the rule's own
    test stays green. Of the lines the pattern matches, the one switched off must
    be the nearest to the rule's marker.
    """
    mutate = _mutate()
    astray = []
    for rule in mutate.all_rows():
        lines = (ROOT / rule["file"]).read_text(encoding="utf-8").splitlines()
        hits = [i for i, line in enumerate(lines) if re.search(str(rule["find"]), line)]
        chosen = mutate.line_of("\n".join(lines), str(rule["find"]), int(rule.get("occurrence", 1)))
        marks = [i for i, line in enumerate(lines) if (m := MARKER.search(line)) and m.group(1) == rule["name"]]
        if marks and min(hits, key=lambda i: abs(i - marks[0])) != chosen:
            astray.append(f"{rule['name']} (line {chosen + 1}; give it an occurrence)")
    assert not astray, "rows that switch off a line away from their marker: " + "; ".join(astray)


def _row(name, file="src/claude_voice/store.py"):
    return f"[[rule]]\nname = \"{name}\"\nfile = \"{file}\"\nfind = 'x'\nreplace = 'y'\n"


def test_a_row_in_a_file_of_its_own_is_read_with_the_table(tmp_path):
    """Two pull requests that each add a row at the end of one file collide; a file
    per row cannot. Until the old rows move, both places are read."""
    table, folder = tmp_path / "mutations.toml", tmp_path / "mutations"
    table.write_text(_row("one in the table"), encoding="utf-8")
    (folder / "store").mkdir(parents=True)
    (folder / "store" / "one-in-a-file.toml").write_text(_row("one in a file"), encoding="utf-8")
    names = [rule["name"] for rule in _mutate().all_rows(table, folder)]
    assert names == ["one in the table", "one in a file"]


def test_the_same_rule_with_two_rows_is_refused(tmp_path):
    """Moving a row and forgetting to delete the old one would switch the rule off twice
    and count it twice."""
    table, folder = tmp_path / "mutations.toml", tmp_path / "mutations"
    table.write_text(_row("twice"), encoding="utf-8")
    (folder / "store").mkdir(parents=True)
    (folder / "store" / "twice.toml").write_text(_row("twice"), encoding="utf-8")
    try:
        _mutate().all_rows(table, folder)
    except SystemExit as refused:
        assert "twice" in str(refused)
        assert "delete" in str(refused)
    else:
        raise AssertionError("a rule with two rows was read")


def test_a_row_file_sits_in_its_module_s_folder_under_its_own_name(tmp_path):
    """One row per file, the file named after the rule, in the folder named after the
    module it switches off: then where a row lives is never a choice, and two pull
    requests only meet when they add the same rule."""
    folder = tmp_path / "mutations"
    (folder / "store").mkdir(parents=True)
    (folder / "api").mkdir()
    (folder / "store" / "in-place.toml").write_text(_row("in place"), encoding="utf-8")
    (folder / "api" / "wrong-folder.toml").write_text(_row("wrong folder"), encoding="utf-8")
    (folder / "store" / "a-wrong-name.toml").write_text(_row("named otherwise"), encoding="utf-8")
    (folder / "store" / "two-rows.toml").write_text(_row("two rows") + _row("in one file"), encoding="utf-8")
    found = " ".join(_mutate().misfiled(folder))
    assert "api/wrong-folder.toml should be store/wrong-folder.toml" in found
    assert "store/a-wrong-name.toml should be store/named-otherwise.toml" in found
    assert "store/two-rows.toml holds 2 rows" in found
    assert "in-place" not in found


def test_every_row_file_in_the_repository_is_where_its_rule_says():
    misfiled = _mutate().misfiled()
    assert not misfiled, "row files out of place: " + "; ".join(misfiled)
