"""When main's own suite is red at a pull request's base, the ratchet says so in its own words,
instead of failing as if the pull request had made something worse (#408). It still fails."""

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / "scripts"))

import ratchet

BASE = "177e495d0c1e8f3a2b4c6d8e0f1a3b5c7d9e1f2a"
RED = (
    "The suite fails; the ratchet measures a green tree.\n"
    "....F..\n"
    "FAILED tests/test_ci.py::test_the_gate_waits_for_every_other_job - AssertionError\n"
    "FAILED tests/test_ci.py::test_another - AssertionError\n"
    "2 failed, 5000 passed in 300s\n"
)


def red_base(monkeypatch, tmp_path, said=RED):
    """A base whose measure run stops because its own suite fails."""
    monkeypatch.setattr(ratchet.tempfile, "tempdir", str(tmp_path))

    def run(*args, **kwargs):
        if "measure" in args:
            return ratchet.subprocess.CompletedProcess(args, 1, "", said)
        return ratchet.subprocess.CompletedProcess(args, 0, "abc123\n", "")

    monkeypatch.setattr(ratchet, "_run", run)


def test_a_base_whose_own_suite_is_red_is_said_to_be_unmeasurable(monkeypatch, tmp_path):
    red_base(monkeypatch, tmp_path)
    with pytest.raises(ratchet.BaseUnmeasured) as caught:
        ratchet.measure_at(BASE)
    assert caught.value.first == "tests/test_ci.py::test_the_gate_waits_for_every_other_job"


def test_another_failure_measuring_the_base_is_reported_as_before(monkeypatch, tmp_path):
    red_base(monkeypatch, tmp_path, said="Traceback: something else broke\n")
    with pytest.raises(SystemExit) as caught:
        ratchet.measure_at(BASE)
    assert not isinstance(caught.value, ratchet.BaseUnmeasured)
    assert "Measuring" in str(caught.value.code)


def test_the_check_names_mains_red_suite_and_not_the_pull_request(monkeypatch, tmp_path, capsys):
    red_base(monkeypatch, tmp_path)
    measured_now = []
    monkeypatch.setattr(ratchet, "measure", lambda *a, **k: measured_now.append(a) or {})
    monkeypatch.setattr(ratchet, "sweep", lambda: None)
    monkeypatch.setattr(sys, "argv", ["ratchet.py", "check", "--base", BASE])
    code = ratchet.main()
    said = capsys.readouterr().err
    assert code == ratchet.BASE_UNMEASURED and code not in (0, 1)
    assert "base 177e495 can't be measured" in said
    assert "tests/test_ci.py::test_the_gate_waits_for_every_other_job" in said
    assert "not this pull request's fault" in said and "Fix main first" in said
    assert "merge past" not in said  # the log is public; it never reads as leave to skip the gate
    assert measured_now == []  # nothing is compared with a base that has no levels


def test_a_red_base_with_no_failing_test_named_still_says_whose_it_is(monkeypatch, tmp_path, capsys):
    red_base(monkeypatch, tmp_path, said="The suite fails; the ratchet measures a green tree.\nERROR collecting\n")
    monkeypatch.setattr(ratchet, "sweep", lambda: None)
    monkeypatch.setattr(sys, "argv", ["ratchet.py", "check", "--base", "origin/main"])
    assert ratchet.main() == ratchet.BASE_UNMEASURED
    said = capsys.readouterr().err
    assert "base origin/main can't be measured" in said and "the log above" in said
