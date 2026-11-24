"""A mutation run that is stopped half way can leave a rule switched off in the source (#432).

The run swaps the rule's line for the row's `replace` line, runs the tests, and puts the line
back. Stopped in between, the swapped line stays. On 21.11 one such `if False:` was committed,
turning off a new check that the pull request said was tested. Two reviewers caught it by eye.
"""

import importlib.util
import pathlib

ROOT = pathlib.Path(__file__).parent.parent


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


mutate = _load("mutate", "scripts/mutate.py")
#: The one copy of the check, shared with every plugin through voice-checks (#515).
shared = _load("voice_checks_left_behind", "packages/voice-checks/src/voice_checks/left_behind.py")

ROW = {"name": "a late answer is refused", "find": r"^\s*if late:$", "replace": "if False:"}


def source(*body):
    return "\n".join(
        ["def check(late):", "    # RULE: a late answer is refused", *body, "", "def other():", "    pass"]
    )


def test_a_rows_replacement_left_beside_its_marker_is_found():
    text = source("    if False:", "        raise ValueError")
    assert shared.left_behind(text, ROW) == 2  # counted from 0: the line under the marker


def test_the_rules_own_line_beside_its_marker_is_not_a_leftover():
    assert shared.left_behind(source("    if late:", "        raise ValueError"), ROW) is None


def test_a_line_like_the_replacement_above_the_rules_own_line_is_not_a_leftover():
    row = {**ROW, "replace": "held = False"}
    text = source("    held = False", "    if late:", "        held = True")
    assert shared.left_behind(text, row) is None


def test_a_leftover_is_found_though_the_pattern_matches_a_line_elsewhere():
    """Today's checks only notice when the pattern then matches nothing at all."""
    far = "\n".join(["", *["x = 1"] * 10, "def again(late):", "    if late:", "        pass", ""])
    text = source("    if False:", "        raise ValueError") + far
    assert shared.left_behind(text, ROW) == 2


def test_a_rules_own_line_just_above_its_marker_is_not_a_leftover():
    """Some rules mark the line before them, or a helper whose caller holds the line."""
    text = "def check(late):\n    if late:\n    # RULE: a late answer is refused\n        if False:\n"
    assert shared.left_behind(text, ROW) is None


def test_a_rule_with_no_marker_in_the_file_has_no_leftover():
    assert shared.left_behind("if False:\n", ROW) is None


#: Rows whose line sits too far from their marker for a leftover to be told apart. Today's
#: check that every pattern still matches its line catches these when it matches nowhere.
UNSEEN = {
    "a name prefix ends in a dash and is at least four characters",
    "a wrong hook command still says stop and exits 0",
    "whats_new carries unread replies even without a cursor",
    "roles add refuses a start line of its own",
    "an event from a place the bridge does not know is refused",
    "confirm_name is part of the signed payload",
}


def test_every_rows_switched_off_line_is_found_when_left_behind():
    missed = set()
    for rule in mutate.all_rows():
        text = (ROOT / rule["file"]).read_text(encoding="utf-8")
        if shared.left_behind(mutate.switched_off(text, rule), rule) is None:
            missed.add(rule["name"])
    assert missed - UNSEEN == set(), "move each rule's marker beside the line its row switches off"
    assert UNSEEN - missed == set(), "these are found now; take them off UNSEEN"


def test_no_rows_replacement_is_left_in_its_file():
    found = []
    for rule in mutate.all_rows():
        at = shared.left_behind((ROOT / rule["file"]).read_text(encoding="utf-8"), rule)
        if at is not None:
            found.append(f"{rule['file']}:{at + 1} still says {rule['replace']!r} for '{rule['name']}'")
    assert not found, "a mutation run was stopped half way; put each rule's own line back: " + "; ".join(found)
