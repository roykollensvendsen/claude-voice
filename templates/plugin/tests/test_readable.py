"""What the readability warning says, and what it stays quiet about.

It never refuses. Every case here is about whether it speaks.
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / "scripts"))

import readable

SHORT = "feat: Add a thing\n\nA subject must start with a verb now. Added and Adds are refused.\n"
LONG_SENTENCE = (
    "feat: Add a thing\n\nThe linter has a setting for this and as of version one it flags "
    "nothing at all, not one form and not another, so turning it on alone would have been "
    "a rule with no mechanism behind it whatsoever.\n"
)


def test_a_short_body_is_left_alone():
    assert readable.findings(SHORT) == []


def test_a_long_sentence_is_named_with_its_length():
    said = readable.findings(LONG_SENTENCE)
    assert any("words per sentence on average" in x for x in said)
    assert any(x.startswith("a sentence of") for x in said)


def test_a_subject_alone_says_nothing():
    assert readable.findings("chore: Pin the actions\n") == []


def test_trailers_are_not_prose():
    """Counted as prose, enough short trailers would drag any average under the limit.

    The long sentence below warns on its own. Adding trailers must not silence
    it, which is what would happen if they were measured as sentences.
    """
    trailers = "\n" + "".join(f"Reviewed-By: Someone Number {n} Of Many\n" for n in range(12))
    assert readable.findings(LONG_SENTENCE + trailers) == readable.findings(LONG_SENTENCE)


def test_a_code_block_is_not_prose():
    """A pasted command has no sentence length worth measuring."""
    fenced = SHORT + "\n```\nuv run pytest -q --cov --this --that --and --the --other --thing --here\n```\n"
    assert readable.findings(fenced) == readable.findings(SHORT)


def test_a_very_long_body_is_named():
    body = "feat: Add a thing\n\n" + ("A short sentence here. " * 90)
    assert any("words; about" in x for x in readable.findings(body))


def test_it_never_refuses(capsys, tmp_path):
    """A gate that refuses a message nobody can shorten teaches --no-verify."""
    path = tmp_path / "COMMIT_EDITMSG"
    path.write_text(LONG_SENTENCE)
    sys.argv = ["readable.py", "--file", str(path)]
    assert readable.main() == 0
    assert capsys.readouterr().out
