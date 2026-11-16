"""Run the commands the documentation shows, and compare what they print.

The maintained plugins for testing markdown run a block and check that it
exited zero; none of them compares the output a document puts in front of a
reader, which is the part that goes wrong silently. This extractor is forty
lines and has no dependency, so it is written here rather than reached for.

A block fenced as ``console`` is run. Lines beginning ``$ `` are commands, and
everything until the next command is what the document says they print.

There is no silent way out. A block that looks like commands and is not
fenced ``console`` must carry ``<!-- not run: <reason> -->`` on the line
above, or the suite fails; choosing a different fence is otherwise an opt-out
nobody sees. Every verb the command has must appear in at least one block that
is run, so a verb cannot be added without a worked example.
"""

import json
import os
import pathlib
import re
import shutil
import subprocess

import pytest

from claude_voice.cli import COMMANDS

ROOT = pathlib.Path(__file__).parent.parent
# Every page, found rather than listed: a hand-kept list is a page's way out.
# evidence/ and references/ hold material quoted from elsewhere, not this
# project's own claims, so their examples are nobody here's to run.
SKIP = ("evidence", "references", ".venv", "node_modules")
LOOKS_LIKE_A_COMMAND = re.compile(
    r"^\s*\$?\s*(claude-voice|uv |git |pytest|python|committed|ruff|mypy)\b", re.MULTILINE
)
NOT_RUN = re.compile(r"<!--\s*not run:\s*(.+?)\s*-->\s*$")


def pages() -> list[pathlib.Path]:
    """Every Markdown page that could carry a command, as git tracks them.

    Asking git rather than walking the folder leaves out what is not part of this checkout:
    a page not yet added, and the checkouts other sessions keep under .claude/worktrees/,
    each holding every page of some other branch.
    """
    tracked = subprocess.run(
        ["git", "ls-files", "-z", "--", "*.md"],  # noqa: S607
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    # Sorted as git names them, so the order, and the test ids, are the same on every platform:
    # Windows compares paths without regard to case.
    names = sorted(name for name in tracked.split("\0") if name)
    found = (pathlib.PurePosixPath(name) for name in names)
    return [ROOT / p for p in found if not any(part in SKIP or part.startswith(".venv") for part in p.parts)]


def _environment(tmp_path: pathlib.Path) -> dict[str, str]:
    """The suite's environment, with a settings file that names an empty folder as the bridge's
    own clone. Without it the command reads the programme file of the checkout it runs in."""
    clone = tmp_path / "clone"
    clone.mkdir(exist_ok=True)
    settings = tmp_path / "settings.toml"
    settings.write_text(f"[paths]\nclaude_voice = {json.dumps(str(clone))}\n", encoding="utf-8")
    return {**os.environ, "CLAUDE_VOICE_CONFIG": str(settings)}


def examples() -> list[tuple[str, str, str]]:
    """Every documented command, with the page it is on and what it should print."""
    found = []
    for page in pages():
        name = str(page.relative_to(ROOT))
        text = page.read_text(encoding="utf-8")
        for block in _console_blocks(text):
            command, expected = None, []
            for line in block.splitlines():
                if line.startswith("$ "):
                    if command is not None:
                        found.append((name, command, "\n".join(expected)))
                    command, expected = line[2:], []
                elif command is not None:
                    expected.append(line)
            if command is not None:
                found.append((name, command, "\n".join(expected)))
    return found


def _console_blocks(text: str) -> list[str]:
    blocks, inside, current = [], False, []
    for line in text.splitlines():
        if line.startswith("```console"):
            inside, current = True, []
        elif line.startswith("```") and inside:
            inside = False
            blocks.append("\n".join(current))
        elif inside:
            current.append(line)
    return blocks


CASES = examples()


def _git(cwd: pathlib.Path, *args: str) -> None:
    subprocess.run(  # noqa: S603
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],  # noqa: S607
        cwd=cwd,
        capture_output=True,
        check=True,
    )


def test_only_the_pages_git_tracks_here_are_run(tmp_path, monkeypatch):
    """A page not yet added, and another session's checkout under .claude/worktrees/, are not ours."""
    _git(tmp_path, "init", "-q")
    for page in ("README.md", "docs/guide.md", "evidence/quoted.md", "draft.md"):
        (tmp_path / page).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / page).write_text("# a page\n", encoding="utf-8")
    _git(tmp_path, "add", "README.md", "docs/guide.md", "evidence/quoted.md")
    other = tmp_path / ".claude" / "worktrees" / "agent-1"
    other.mkdir(parents=True)
    _git(other, "init", "-q")
    (other / "README.md").write_text("# another branch's page\n", encoding="utf-8")
    _git(other, "add", "README.md")
    monkeypatch.setitem(globals(), "ROOT", tmp_path)

    found = [p.relative_to(tmp_path).as_posix() for p in pages()]

    assert found == ["README.md", "docs/guide.md"]


def test_the_documentation_shows_at_least_one_command():
    """A page that stops showing its own output would otherwise pass by saying nothing."""
    assert CASES, "no ```console block anywhere; the examples were removed or mislabelled"


@pytest.mark.skipif(shutil.which("claude-voice") is None, reason="the command is not installed")
@pytest.mark.parametrize(("page", "command", "expected"), CASES, ids=[c[1][:40] for c in CASES])
def test_a_documented_command_prints_what_the_page_says(page: str, command: str, expected: str, tmp_path):
    env = _environment(tmp_path)
    r = subprocess.run(command, shell=True, capture_output=True, text=True, cwd=ROOT, env=env, check=False)  # noqa: S602
    got = (r.stdout + r.stderr).strip()
    assert got == expected.strip(), f"{page} says this prints:\n{expected}\nit prints:\n{got}"


def test_a_documented_command_never_reads_the_programme_file_of_the_checkout(tmp_path):
    """The commands run in this checkout, where a clone the bridge updates keeps its owner's own
    programme file, which git leaves out. A documented command must print the same without it."""
    from claude_voice import config

    checkout = tmp_path / "checkout"
    (checkout / ".claude").mkdir(parents=True)
    (checkout / ".claude" / "org.toml").write_text("this is not toml [")

    clone = config.load(config.FILE, own_clone=checkout, env=_environment(tmp_path)).paths.claude_voice

    assert clone is not None and clone != checkout
    assert not (clone / ".claude" / "org.toml").exists()


def test_no_block_of_commands_escapes_by_choosing_another_fence():
    """A block that looks like commands is run, or says in the document why it is not."""
    escaped = []
    for page in pages():
        text = page.read_text(encoding="utf-8")
        for match in re.finditer(r"^```(\w*)\n(.*?)^```", text, re.DOTALL | re.MULTILINE):
            fence, body = match.group(1), match.group(2)
            if fence == "console" or not LOOKS_LIKE_A_COMMAND.search(body):
                continue
            before = text[: match.start()].splitlines()
            preceding = next((x for x in reversed(before) if x.strip()), "")
            if not NOT_RUN.search(preceding):
                first = next((x for x in body.splitlines() if x.strip()), "")
                escaped.append(f"{page.relative_to(ROOT)}: {first.strip()[:50]}")
    assert not escaped, "fence these console and let them run, or say why they cannot:\n" + "\n".join(escaped)


def test_every_verb_the_command_has_appears_in_a_worked_example():
    """A verb added without one would be documented only by its own help text.

    Delete this test for a project that is not a command-line tool.
    """
    documented = " ".join(command for _, command, _ in CASES)
    missing = [v for v in COMMANDS if f"claude-voice {v}" not in documented and f" {v} " not in documented]
    assert not missing, f"no run example uses: {', '.join(missing)}"
