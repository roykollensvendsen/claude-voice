"""Every rule this project enforces has evidence, and the evidence names it.

A rule here is one line of source marked ``# RULE: what it says``. Three things
have to be true of each, and none of them is true by writing the rule:

* it has a row in ``scripts/mutations.toml``, so something switches it off;
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
import tomllib

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
    """Every rule the mutation table claims to switch off."""
    table = tomllib.loads((ROOT / "scripts/mutations.toml").read_text(encoding="utf-8"))
    return {str(rule["name"]) for rule in table.get("rule", [])}


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
    table = tomllib.loads((ROOT / "scripts/mutations.toml").read_text(encoding="utf-8"))
    broken = []
    for rule in table.get("rule", []):
        text = (ROOT / rule["file"]).read_text(encoding="utf-8")
        why = mutate.broken(rule["file"], mutate.switched_off(text, rule))
        if why:
            broken.append(f"{rule['name']}: {why}")
    assert not broken, "rows whose replacement is not a whole line of valid code: " + "; ".join(broken)
