"""`voice-checks bump`: one pin, moved by one tool, in each plugin's pyproject.toml."""

import subprocess

import pytest

from voice_checks import bump

OLD = "a" * 40
NEW = "b" * 40
PIN = "voice-checks @ git+https://github.com/roykollensvendsen/claude-voice@{}#subdirectory=packages/voice-checks"
PYPROJECT = f'[dependency-groups]\ndev = [\n    "pytest>=9",\n    "{PIN.format(OLD)}",\n]\n'


def test_the_pin_moves_to_the_given_commit_and_nothing_else_changes():
    assert bump.moved(PYPROJECT, NEW) == PYPROJECT.replace(OLD, NEW)


def test_a_pyproject_with_no_pin_is_refused():
    with pytest.raises(bump.BumpError, match="no voice-checks pin"):
        bump.moved('[dependency-groups]\ndev = ["pytest>=9"]\n', NEW)


def test_a_commit_that_is_not_forty_hex_characters_is_refused():
    with pytest.raises(bump.BumpError, match="40"):
        bump.moved(PYPROJECT, "main")


def test_the_newest_release_tag_is_found_by_its_version_not_its_name():
    listed = (
        f"{'1' * 40}\trefs/tags/voice-checks-v0.9.0\n"
        f"{'2' * 40}\trefs/tags/voice-checks-v0.10.0\n"
        f"{'3' * 40}\trefs/tags/voice-checks-v0.10.0^{{}}\n"
    )
    # 0.10 is newer than 0.9, and an annotated tag's commit is the line marked ^{}.
    assert bump.newest_release(listed) == "3" * 40


def test_no_release_tag_yet_says_how_to_pin_by_hand():
    with pytest.raises(bump.BumpError, match="--sha"):
        bump.newest_release("")


@pytest.mark.parametrize(
    "failure",
    [
        FileNotFoundError("git"),
        subprocess.CalledProcessError(128, ["git", "ls-remote"], stderr="could not resolve host"),
        subprocess.TimeoutExpired(["git", "ls-remote"], 60),
    ],
    ids=["no git", "no network", "too slow"],
)
def test_a_failed_look_for_releases_says_what_to_do(monkeypatch, failure):
    def fails(*_args, **_kwargs):
        raise failure

    monkeypatch.setattr(bump.subprocess, "run", fails)
    with pytest.raises(bump.BumpError, match="--sha"):
        bump.latest()


def test_bump_writes_the_new_pin(tmp_path):
    path = tmp_path / "pyproject.toml"
    path.write_text(PYPROJECT, encoding="utf-8")
    bump.bump(path, NEW)
    assert PIN.format(NEW) in path.read_text(encoding="utf-8")
