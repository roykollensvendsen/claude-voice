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
    """Every Markdown page that could carry a command, found by walking."""
    return sorted(
        p for p in ROOT.rglob("*.md") if not any(part in SKIP or part.startswith(".venv") for part in p.parts)
    )


def examples() -> list[tuple[str, str, str]]:
    """Every documented command, with the page it is on and what it should print."""
    found = []
    for page in pages():
        name = str(page.relative_to(ROOT))
        text = page.read_text()
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


def test_the_documentation_shows_at_least_one_command():
    """A page that stops showing its own output would otherwise pass by saying nothing."""
    assert CASES, "no ```console block anywhere; the examples were removed or mislabelled"


@pytest.mark.skipif(shutil.which("claude-voice") is None, reason="the command is not installed")
@pytest.mark.parametrize(("page", "command", "expected"), CASES, ids=[c[1][:40] for c in CASES])
def test_a_documented_command_prints_what_the_page_says(page: str, command: str, expected: str):
    r = subprocess.run(command, shell=True, capture_output=True, text=True, cwd=ROOT, check=False)  # noqa: S602
    got = (r.stdout + r.stderr).strip()
    assert got == expected.strip(), f"{page} says this prints:\n{expected}\nit prints:\n{got}"


def test_no_block_of_commands_escapes_by_choosing_another_fence():
    """A block that looks like commands is run, or says in the document why it is not."""
    escaped = []
    for page in pages():
        text = page.read_text()
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
