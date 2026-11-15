#!/usr/bin/env python3
"""The code a check measures is this checkout's own, not another checkout's installed copy.

The suite reads the package from ``src/`` (pytest's ``pythonpath``). An environment shared with
another worktree can load that worktree's copy first, by an install hook, and then the mutation
run switches a rule off here while the tests measure the code there: every rule would look
unguarded, or worse, guarded. So before measuring, the interpreter that runs the suite is
asked, in a process of its own with the same environment, where the package comes from.

The scripts that use this never import the package themselves: that import would be this
process's, not the suite's.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

#: The package every check measures.
MODULE = "gizmo_mcp"
#: What the suite does: src/ first on the path, then the import.
ASK = f"import sys; sys.path.insert(0, 'src'); import {MODULE}; print({MODULE}.__file__)"


def require(root: pathlib.Path, python: str, env: dict[str, str]) -> None:
    """Stop, naming both places, unless `python` imports the package from `root`'s own src/."""
    source = (root / "src").resolve()
    asked = subprocess.run([python, "-c", ASK], cwd=root, env=env, capture_output=True, text=True, check=False)
    if asked.returncode:
        msg = (
            f"{python} cannot import {MODULE} from {source}:\n{asked.stderr[-1000:]}"
            f"Run `uv sync` in {root}, then run this again."
        )
        raise SystemExit(msg)
    found = pathlib.Path(asked.stdout.strip()).resolve()
    # RULE: a check measures only this checkout's own code
    if not found.is_relative_to(source):
        msg = (
            f"{MODULE} would be measured from {found.parent}, not from {source}: another checkout's"
            f" environment is in the way. Give {root} an environment of its own (`uv sync` there,"
            " with VIRTUAL_ENV and UV_PROJECT_ENVIRONMENT unset), and run this again."
        )
        raise SystemExit(msg)


if __name__ == "__main__":
    sys.stdout.write(__doc__ or "")
