"""The owner's own branches, which no cleanup or claim ever touches: one source for every reader.

The list is a file of one branch name per line; a line starting with # is a comment, and a
blank line is skipped. Standard library only, so a script can load it without the package's
other modules.
"""

from __future__ import annotations

import pathlib

NEVER_TOUCH = pathlib.Path("~/.config/claude-voice/never-touch-branches.txt").expanduser()


def never_touch(path: pathlib.Path = NEVER_TOUCH) -> set[str]:
    """The branch names in the list at `path`. A missing file raises, so a caller stops."""
    lines = path.read_text(encoding="utf-8").splitlines()
    return {line.strip() for line in lines if line.strip() and not line.strip().startswith("#")}
