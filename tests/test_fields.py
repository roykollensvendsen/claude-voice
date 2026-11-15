"""The answers the owner types into the terminal: what fits, and asking again when one does not."""

import io

import pytest

from claude_voice import fields
from claude_voice.fields import Field, Refused


class Stream(io.StringIO):
    def __init__(self, tty):
        super().__init__()
        self.tty = tty

    def isatty(self):
        return self.tty


def test_questions_are_asked_only_in_a_real_terminal():
    yes, no = Stream(True), Stream(False)
    assert fields.in_terminal({}, yes, yes)
    assert not fields.in_terminal({}, no, yes)  # a pipe in
    assert not fields.in_terminal({}, yes, no)  # a pipe out
    assert not fields.in_terminal({"CLAUDECODE": "1"}, yes, yes)  # from inside Claude Code


def test_an_answer_outside_its_range_is_refused():
    minutes = Field("int", range=(0, 1440))
    assert minutes.parse(" 30 ") == 30
    with pytest.raises(Refused, match="a whole number from 0 to 1440"):
        minutes.parse("1441")
    with pytest.raises(Refused):
        minutes.parse("1,5")


def test_a_number_takes_a_decimal_comma_and_keeps_the_decimals_it_is_given():
    latitude = Field("float", range=(57, 72), round=2)
    assert latitude.parse("59,91234") == 59.91
    assert latitude.parse("59.919") == 59.92
    for bad in ("nan", "inf", "north", ""):
        with pytest.raises(Refused):
            latitude.parse(bad)


def test_text_and_choices_fit_only_as_the_manifest_says():
    contact = Field("str", max_len=5)
    assert contact.parse(" abc ") == "abc"
    with pytest.raises(Refused, match="at most 5"):
        contact.parse("abcdef")
    with pytest.raises(Refused):
        contact.parse("  ")
    fuel = Field("choice", values=("petrol", "diesel"))
    assert fuel.parse("diesel") == "diesel"
    with pytest.raises(Refused, match="one of: petrol, diesel"):
        fuel.parse("coal")


def test_a_refused_answer_is_asked_again_naming_what_fits():
    typed, said = iter(["2000", "abc", "45"]), []
    value = fields.ask(Field("int", range=(0, 1440)), "Minutes? ", lambda prompt: next(typed), said.append)
    assert value == 45
    assert len(said) == 2 and all("from 0 to 1440" in s for s in said)
    assert not any("2000" in s or "abc" in s for s in said)  # the answer itself is never shown
