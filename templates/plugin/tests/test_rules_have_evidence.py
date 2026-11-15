"""Every rule this project enforces has evidence, and the evidence names it.

A rule here is one line of source marked ``# RULE: what it says``. Three things
have to be true of each, and none of them is true by writing the rule:

* it has a row of its own under ``scripts/mutations/``, so something switches it off;
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

import pytest

ROOT = pathlib.Path(__file__).parent.parent
#: The project's own tools, the mutation runner included: their rules are switched off like
#: the source's. The runner has loaded its code before it switches a line off, and the suite
#: it starts loads the switched-off file, so it can prove its own rules too.
OWN_SCRIPTS = tuple(sorted((ROOT / "scripts").glob("*.py")))
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
    for source in [*sorted((ROOT / "src").rglob("*.py")), *OWN_SCRIPTS]:
        for line in source.read_text().splitlines():
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
        found.update(re.findall(r"^(?:async )?def (test_\w+)", module.read_text(), re.MULTILINE))
    return found


def test_every_rule_in_the_source_has_a_row_in_the_mutation_table():
    """Without this the evidence falls behind the code and nobody sees it happen."""
    marked, rowed = marked_rules(), rowed_rules()
    assert marked, "no rule marked anywhere; the marker or the places it is looked for are wrong"
    assert marked - rowed == set(), f"marked in the source and never switched off: {marked - rowed}"
    assert rowed - marked == set(), f"a row for a rule no longer in the source: {rowed - marked}"


def test_every_rule_has_a_test_named_after_it():
    """A kill is only evidence about a rule when the test that went red names it."""
    name_for = _mutate().test_name_for
    named = defined_test_names()
    missing = sorted(rule for rule in marked_rules() if name_for(rule) not in named)
    wanted = ", ".join(f"{name_for(rule)}()" for rule in missing)
    assert not missing, f"rules with no test named after them — write {wanted}"


def test_every_row_leaves_code_that_compiles():
    """The runner replaces the whole line, keeping only its indentation.

    A row written as if it swapped part of the line leaves a line cut off mid-expression: the
    module no longer imports, every test in the file errors, and the run reports the rule as
    killed by another test instead of naming the row.
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
    """Of the lines a row's pattern matches, the one switched off must be the nearest to its marker.

    New code with a line like the rule's, placed above it in the same file, would otherwise take
    the first match, and the rule's own test would stay green.
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


def _row(name, file="src/gizmo_mcp/server.py"):
    return f"[[rule]]\nname = \"{name}\"\nfile = \"{file}\"\nfind = 'x'\nreplace = 'y'\n"


def _place(folder, path, text):
    (folder / path).parent.mkdir(parents=True, exist_ok=True)
    (folder / path).write_text(text)


def test_every_row_is_read_from_a_file_of_its_own(tmp_path):
    """A file per row only meets another pull request adding the same rule.

    Two pull requests that each added a row at the end of one shared file collided.
    """
    folder = tmp_path / "mutations"
    _place(folder, "src/gizmo_mcp/server.py/one.toml", _row("one"))
    _place(folder, "src/gizmo_mcp/status.py/two.toml", _row("two", "src/gizmo_mcp/status.py"))
    assert [rule["name"] for rule in _mutate().all_rows(folder)] == ["one", "two"]


def test_the_same_rule_with_two_rows_is_refused(tmp_path):
    """A rule with two rows would be switched off twice and counted twice."""
    folder = tmp_path / "mutations"
    _place(folder, "src/gizmo_mcp/server.py/twice.toml", _row("twice"))
    _place(folder, "src/gizmo_mcp/cache.py/twice.toml", _row("twice", "src/gizmo_mcp/cache.py"))
    with pytest.raises(SystemExit) as refused:
        _mutate().all_rows(folder)
    said = str(refused.value)
    assert "mutations/src/gizmo_mcp/server.py/twice.toml" in said
    assert "mutations/src/gizmo_mcp/cache.py/twice.toml" in said
    assert "delete one" in said


def test_a_rule_with_no_letter_or_digit_is_refused_a_row_file():
    with pytest.raises(SystemExit, match="no letter or digit"):
        _mutate().row_path({"name": "--", "file": "src/gizmo_mcp/server.py"})


def test_a_row_file_sits_under_its_source_file_s_whole_path_under_its_own_name(tmp_path):
    """Where a row lives is never a choice.

    Its folder is the row's `file` exactly, extension included; its name is the rule's words.
    """
    folder = tmp_path / "mutations"
    _place(folder, "src/gizmo_mcp/server.py/in-place.toml", _row("in place"))
    _place(folder, "server/wrong-folder.toml", _row("wrong folder"))
    _place(folder, "src/gizmo_mcp/server.py/a-wrong-name.toml", _row("named otherwise"))
    _place(folder, "src/gizmo_mcp/server.py/two-rows.toml", _row("two rows") + _row("in one file"))
    found = " ".join(_mutate().misfiled(folder))
    assert "server/wrong-folder.toml should be src/gizmo_mcp/server.py/wrong-folder.toml" in found
    here = "src/gizmo_mcp/server.py"
    assert f"{here}/a-wrong-name.toml should be {here}/named-otherwise.toml" in found
    assert "src/gizmo_mcp/server.py/two-rows.toml holds 2 rows" in found
    assert "in-place" not in found


def test_every_row_file_in_the_repository_is_where_its_rule_says():
    misfiled = _mutate().misfiled()
    assert not misfiled, "row files out of place: " + "; ".join(misfiled)
    assert not (ROOT / "scripts/mutations.toml").exists(), "the shared table is gone; give each row a file"
