"""The setup guide and the README's step list it makes: moved from messages-mcp's and ams-mcp's
scripts/docs.py and tests/test_docs.py (no-duplicates-v1, note n12)."""

import pathlib

import pytest

from voice_checks import docs
from voice_checks.cli import main

GUIDE = """# A plugin

## Steps

### 1. Install it

<!-- safe-to-run -->
```
echo installed
```

Check: `echo ok` prints ok.

### 2. Start it

```
systemctl --user start it
```

Check: `systemctl --user is-active it` prints active.

## Steps only the owner can do

### A. Log in

<!-- owner -->
Why: it is a login.

Check: the app says logged in.
"""
NO_OWNER_STEP = GUIDE.split("### A.", maxsplit=1)[0] + "None.\n"


def _plugin(tmp_path, guide=GUIDE, readme=None):
    """A plugin checkout with a guide, and a README whose block the guide made unless one is given."""
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs/setup.md").write_text(guide, encoding="utf-8")
    if readme is None:
        readme = docs.updated("# A plugin\n\n<!-- steps:begin -->\n<!-- steps:end -->\n", guide)
    (tmp_path / "README.md").write_text(readme, encoding="utf-8")
    return tmp_path


def test_the_readme_lists_each_agent_step_by_its_first_line_and_each_owner_step_in_full():
    made = docs.block(GUIDE)
    assert "1. Install it\n2. Start it\n" in made
    assert "systemctl --user start it" not in made
    assert "#### A. Log in\n\nWhy: it is a login.\n\nCheck: the app says logged in.\n" in made
    assert "<!-- owner -->" not in made
    assert docs.block(GUIDE.split("### A.", maxsplit=1)[0]).count("None.") == 1


def test_only_a_block_marked_safe_to_run_is_ever_run():
    blocks = [(s.number, b.safe) for s in docs.steps(GUIDE) for b in s.blocks]
    assert blocks == [("1", True), ("2", False)]
    assert docs.safe_blocks(GUIDE) == [("1", "echo installed")]


def test_a_well_written_guide_has_nothing_to_say(tmp_path):
    assert docs.problems(_plugin(tmp_path)) == []


def test_a_guide_whose_owner_part_says_none_has_nothing_to_say(tmp_path):
    """The heading must hold an owner step or the word None; the copies refused None by mistake."""
    assert docs.problems(_plugin(tmp_path, NO_OWNER_STEP)) == []


@pytest.mark.parametrize(
    ("guide", "said"),
    [
        (GUIDE.replace("Check: `echo ok` prints ok.\n", ""), "step 1 has no Check: line"),
        (GUIDE.replace("```\nsystemctl --user start it\n```\n", ""), "step 2 shows no command"),
        (GUIDE.split("## Steps only the owner can do", maxsplit=1)[0], "no '## Steps only the owner can do'"),
        (GUIDE.split("### A.", maxsplit=1)[0], "holds no step and does not say None."),
        (GUIDE.replace("<!-- owner -->\n", ""), "step A is not marked <!-- owner -->"),
        (GUIDE.replace("Why: it is a login.\n", ""), "step A does not say Why:"),
        (GUIDE.replace("Check: `echo ok`", "<!-- owner -->\nCheck: `echo ok`"), "step 1 is marked <!-- owner -->"),
        ("# A plugin\n", "docs/setup.md has no steps"),
    ],
    ids=[
        "a step with no check",
        "an agent step with no command",
        "no owner heading",
        "an empty owner heading",
        "an owner step not marked",
        "an owner step that says not why",
        "an owner step outside the owner heading",
        "no steps at all",
    ],
)
def test_a_guide_an_agent_cannot_follow_is_refused(tmp_path, guide, said):
    found = docs.problems(_plugin(tmp_path, guide))
    assert any(said in line for line in found), found


def test_a_stale_readme_is_refused_and_says_how_to_fix_it(tmp_path):
    root = _plugin(tmp_path, readme="# A plugin\n\n<!-- steps:begin -->\nold\n<!-- steps:end -->\n")
    found = docs.problems(root)
    assert any("uv run voice-checks docs --write" in line for line in found), found


def test_a_readme_with_no_block_is_refused_and_says_how_to_fix_it():
    with pytest.raises(docs.DocsError, match="add the two markers"):
        docs.updated("# A plugin\n", GUIDE)


def test_the_check_fails_on_a_stale_readme_changes_nothing_and_write_mends_it(tmp_path, capsys):
    root = _plugin(tmp_path, readme="# A plugin\n\n<!-- steps:begin -->\nold\n<!-- steps:end -->\n")
    assert main(["docs", "--root", str(root)]) == 1
    assert "uv run voice-checks docs --write" in capsys.readouterr().out
    assert "old" in (root / "README.md").read_text(encoding="utf-8"), "the check changes nothing"
    assert main(["docs", "--root", str(root), "--write"]) == 0
    assert main(["docs", "--root", str(root)]) == 0


def test_write_still_refuses_a_guide_an_agent_cannot_follow(tmp_path):
    root = _plugin(tmp_path, GUIDE.replace("Why: it is a login.\n", ""))
    assert main(["docs", "--root", str(root), "--write"]) == 1


def test_a_safe_step_runs_with_home_and_its_folders_in_its_own_folder(tmp_path, monkeypatch):
    monkeypatch.setenv("GH_TOKEN", "made-up-token")
    monkeypatch.setenv("SOME_API_KEY", "made-up-key")
    env = docs.isolated_env(tmp_path)
    moved = {"HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME", "TMPDIR"}
    assert set(env) <= {*docs.PASSED_ON, *moved}, "a safe step is given only the variables it needs"
    assert "GH_TOKEN" not in env
    assert "SOME_API_KEY" not in env
    for name in moved:
        assert pathlib.Path(env[name]).is_relative_to(tmp_path), name
        assert pathlib.Path(env[name]).is_dir(), name


def test_the_owner_part_starts_at_the_owner_heading_itself(tmp_path):
    """A deeper heading with the same words earlier in the guide does not start the owner part."""
    empty_owner = GUIDE.split("### A.", maxsplit=1)[0]
    heading = "## Steps only the owner can do\n"
    guide = empty_owner.replace(heading, f"#{heading}\nNone.\n\n{heading}")
    found = docs.problems(_plugin(tmp_path, guide))
    assert any("holds no step and does not say None." in line for line in found), found


def test_a_heading_inside_a_command_block_is_not_a_step():
    guide = GUIDE.replace("echo installed\n", "echo installed\n## not a heading\n### 9. not a step\n")
    assert [s.number for s in docs.steps(guide)] == ["1", "2", "A"]
    assert docs.safe_blocks(guide) == [("1", "echo installed\n## not a heading\n### 9. not a step")]


@pytest.mark.parametrize("missing", ["docs/setup.md", "README.md"])
def test_a_missing_guide_or_readme_is_named_not_a_traceback(tmp_path, capsys, missing):
    root = _plugin(tmp_path)
    (root / missing).unlink()
    assert main(["docs", "--root", str(root)]) == 1
    assert f"{missing} is missing" in capsys.readouterr().out
    assert main(["docs", "--root", str(root), "--write"]) == 1


def test_write_ends_each_line_the_same_on_every_system(tmp_path):
    """Only red on Windows, where text mode would write CRLF and make the README stale on Linux."""
    root = _plugin(tmp_path, readme="# A plugin\n\n<!-- steps:begin -->\nold\n<!-- steps:end -->\n")
    docs.write(root)
    assert b"\r\n" not in (root / "README.md").read_bytes()
