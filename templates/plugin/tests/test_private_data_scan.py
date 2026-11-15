"""Nothing committed says where the owner lives or names what they keep private."""

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / "scripts"))

import private_data_scan


def test_a_home_network_address_is_never_committed():
    # Built at run time, so this file does not itself hold what it forbids.
    for address in (".".join(p) for p in (("10", "0", "0", "7"), ("192", "168", "1", "50"))):
        assert private_data_scan.offences(f"bridge at http://{address}:8000", set()), address
    assert private_data_scan.offences("the made-up gizmo at 192.0.2.7", set()) == []


def test_a_street_address_is_never_committed():
    assert private_data_scan.offences("the shop in " + "Storgata" + " 12", set())
    assert private_data_scan.offences("Kirke" + "veien 3B", set())


def test_coordinates_more_exact_than_two_decimals_are_never_committed():
    assert private_data_scan.offences("home = " + "59.91" + "38, 10.75" + "22", set())
    assert private_data_scan.offences("home = 59.91, 10.75", set()) == []


def test_a_session_link_is_never_committed():
    assert private_data_scan.offences("Claude-" + "Session: x", set())


def test_a_word_from_the_private_list_is_never_committed(tmp_path):
    words = tmp_path / "private.toml"
    words.write_text('words = ["Garasje Bakgarden", "abc"]\n')
    private = private_data_scan.private_words(words)
    assert private == {"Garasje Bakgarden"}, "a word shorter than four letters would match ordinary text"
    assert private_data_scan.offences("the gizmo in garasje bakgarden", private)


def test_no_private_list_at_all_skips_that_check(tmp_path):
    assert private_data_scan.private_words(tmp_path / "missing.toml") == set()


@pytest.mark.parametrize(
    ("kind", "said"),
    [
        ("broken", "cannot be read as TOML"),
        ("folder", "is not a readable file"),
        ("dangling link", "is not a readable file"),
    ],
)
def test_a_private_list_that_cannot_be_read_refuses_the_commit(tmp_path, kind, said):
    """A list that is there but unusable would check nothing and look like a pass; each says its fix."""
    words = tmp_path / "private.toml"
    if kind == "broken":
        words.write_text("words = [unclosed\n")
    elif kind == "folder":
        words.mkdir()
    else:
        words.symlink_to(tmp_path / "gone.toml")
    with pytest.raises(private_data_scan.UnreadableListError) as refused:
        private_data_scan.private_words(words)
    assert str(words) in str(refused.value)
    assert said in str(refused.value)


def test_an_unreadable_list_fails_the_scan_and_says_why(tmp_path, capsys):
    words = tmp_path / "private.toml"
    words.mkdir()
    message = tmp_path / "MSG"
    message.write_text("feat: Add a thing\n")
    assert private_data_scan.main(["--message", str(message), "--words", str(words)]) == 1
    assert "is not a readable file" in capsys.readouterr().err


def test_a_clean_message_passes_and_a_bad_one_is_named_by_kind_only(tmp_path, capsys):
    message = tmp_path / "MSG"
    message.write_text("feat: Add a thing\n\n# a comment git drops: " + "10.0.0" + ".1\n")
    none = str(tmp_path / "none.toml")
    assert private_data_scan.main(["--message", str(message), "--words", none]) == 0
    message.write_text("feat: Add a thing\n\nAt " + "Storgata" + " 12.\n")
    assert private_data_scan.main(["--message", str(message), "--words", none]) == 1
    said = capsys.readouterr().err
    assert "a street address" in said
    assert "Storgata" not in said
