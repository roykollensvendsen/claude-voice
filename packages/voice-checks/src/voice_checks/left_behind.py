"""A rule a stopped mutation run left switched off (claude-voice #514, #515).

A mutation run proves a rule is tested by swapping the rule's line for the row's harmless
replacement, running the tests, and putting the line back. Stopped in between, the swapped line
stays. The check looks near each rule's `# RULE:` marker: when no line there matches the row's
pattern any more, but one is the row's replacement, the run never put the line back.

The replacement on its own proves nothing, since `return None` or `pass` stand in many files on
purpose. Only the first marker with a rule's name is read. Rows are read from the shared table,
scripts/mutations.toml, and from one file per row under scripts/mutations/.
"""

import pathlib
import re
import tomllib

#: A rule's marker in the source.
MARKER = re.compile(r"^[ \t]*#\s*RULE:\s*(.+?)\s*$")
#: How far from its marker, either way, a rule's own line is looked for.
NEAR = 8


def rows(root: pathlib.Path) -> list[dict[str, object]]:
    """Every mutation row in the repository: the shared table first, then one file per row."""
    table, folder = root / "scripts/mutations.toml", root / "scripts/mutations"
    paths = [table] if table.is_file() else []
    paths += sorted(folder.rglob("*.toml")) if folder.is_dir() else []
    return [rule for path in paths for rule in tomllib.loads(path.read_text(encoding="utf-8")).get("rule", [])]


def left_behind(text: str, rule: dict[str, object]) -> int | None:
    """The 0-based line where a stopped run left this rule switched off, or None."""
    lines = text.splitlines()
    marks = [i for i, line in enumerate(lines) if (m := MARKER.search(line)) and m.group(1) == rule["name"]]
    if not marks:
        return None
    near = [i for i in range(max(marks[0] - NEAR, 0), min(marks[0] + NEAR + 1, len(lines))) if i != marks[0]]
    # RULE: a rule whose own line is still near its marker is no leftover
    if any(re.search(str(rule["find"]), lines[i]) for i in near):
        return None
    replacement = str(rule["replace"]).strip()
    below = sorted(near, key=lambda i: (i < marks[0], abs(i - marks[0])))  # below the marker first
    # RULE: a rule left switched off is named with its file and line
    return next((i for i in below if lines[i].strip() == replacement), None)


def scan(root: pathlib.Path) -> list[str]:
    """`file:line: why` for each rule left switched off, and each row whose file is gone."""
    found = []
    for rule in rows(root):
        source = root / str(rule["file"])
        if not source.is_file():
            found.append(f"{rule['file']}: the row for {rule['name']!r} names a file that is not there")
            continue
        line = left_behind(source.read_text(encoding="utf-8"), rule)
        if line is not None:
            found.append(f"{rule['file']}:{line + 1}: {rule['name']!r} is left switched off; put its own line back")
    return found
