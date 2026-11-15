"""How the bridge names its own command in a hint, so the command it suggests runs from any shell.

A hint such as "run: claude-voice plugins approve garage" works only where the shell's search
path finds the program. Where the bridge runs from its own folder, the hint names that folder.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

NAME = "claude-voice"


def _running(argv0: str, prefix: str) -> Path | None:
    """The claude-voice program this process was started as: the one it was run by, or the
    one in its own Python environment when it was started through ``python -m``."""
    for candidate in (Path(argv0), Path(prefix) / "bin" / NAME):
        if candidate.name == NAME and candidate.is_file():
            return candidate.absolute()
    return None


def command(argv0: str | None = None, prefix: str | None = None, home: Path | None = None) -> str:
    """How a hint names the bridge: the bare name when the search path finds this very
    program, otherwise its path, from ~ when it lies under the home folder."""
    program = _running(argv0 or sys.argv[0], prefix or sys.prefix)
    found = shutil.which(NAME)
    if program is None or (found and Path(found).resolve() == program.resolve()):
        return NAME
    home = home or Path.home()
    return f"~/{program.relative_to(home).as_posix()}" if program.is_relative_to(home) else str(program)
