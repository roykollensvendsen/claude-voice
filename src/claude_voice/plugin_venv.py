"""Start a plugin declared as `uv run <script>` from its own environment, synced just before.

`uv run` stays alive as the plugin's parent for as long as the plugin runs, and holds uv's
cache lock all that time, so the cache could never be pruned. So the bridge runs a short
`uv sync --locked` in the plugin's folder, which makes `.venv` match the plugin's lock file and
then exits, and starts the script from `.venv` itself. The environment's files are hard links,
so a later prune of uv's cache leaves the running plugin whole.

Only that one shape is changed: `uv run`, then a plain script name. Every other command starts
as it is written. The approval still covers the command as written, so nothing is approved
again. This runs only for a plugin the owner approved: the approval is checked first.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

from . import procs, uvlock
from .config import STATE_DIR, PluginSpec

#: The lock every step that changes what uv keeps takes first (uvlock).
LOCK = STATE_DIR / "uv.lock"
#: How long a sync may take, waiting for the lock included. A guess: a sync that has to fetch
#: new packages after a pull takes tens of seconds; one with nothing to do, a fraction of one.
SYNC_SECONDS = 300.0
SCRIPT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}")
UV = ("uv", "uv.exe")


class Refused(RuntimeError):
    """A plugin that cannot be started from its environment; the message says how to fix it."""


def find_uv() -> str | None:
    return procs.find("uv")


def script_of(spec: PluginSpec) -> str | None:
    """The script a plugin declares as `uv run <script> ...`, or None for any other command."""
    shaped = Path(spec.command).name in UV and len(spec.args) > 1 and spec.args[0] == "run"
    # RULE: only uv run with a plain script name is started from the plugins own environment
    return spec.args[1] if shaped and SCRIPT.fullmatch(spec.args[1]) and ".." not in spec.args[1] else None


def entry_point(folder: Path, script: str, windows: bool = sys.platform == "win32") -> Path:
    """Where a script lives in the environment in `folder`."""
    venv = folder / ".venv"
    return venv / "Scripts" / f"{script}.exe" if windows else venv / "bin" / script


def environment() -> dict[str, str]:
    """What the sync's uv is given: the MCP library's small default environment, the same the
    plugin itself is started with, so none of the bridge's own variables, nor its secret."""
    from mcp.client.stdio import get_default_environment

    return get_default_environment()


def ready(spec: PluginSpec) -> tuple[str, list[str]] | None:
    """Sync the plugin's environment and say how to start it from there: the program and its
    arguments, or None to start the plugin as its command is written. Refused, naming the
    folder and the command that fixes it, when the environment cannot be made ready."""
    script = script_of(spec)
    if script is None:
        return None
    folder = Path(spec.cwd)
    fix = f"run `uv sync --locked` in {folder}"
    uv = find_uv()
    if uv is None:
        raise Refused(f"{spec.name} cannot be synced: uv is not installed; install it, then {fix}")
    try:
        # RULE: a sync waits its turn behind other uv work
        with uvlock.held(LOCK, SYNC_SECONDS):
            done = subprocess.run(
                [uv, "sync", "--locked"],
                cwd=folder,
                # RULE: a sync gets none of the bridges secrets
                env=environment(),
                capture_output=True,
                text=True,
                timeout=SYNC_SECONDS,
                check=False,
            )
    except uvlock.Busy as exc:
        raise Refused(f"{spec.name} was not synced: {exc}; it is tried again on the next start") from None
    except subprocess.TimeoutExpired:
        raise Refused(f"{spec.name}'s sync did not finish within {SYNC_SECONDS:g} s; {fix}") from None
    except OSError as exc:
        raise Refused(f"{spec.name}'s sync could not run ({exc}); {fix}") from None
    # RULE: a plugin whose sync fails is refused with the command to fix it
    if done.returncode != 0:
        said = (done.stderr or done.stdout).strip().splitlines()
        raise Refused(f"{spec.name}'s sync failed ({said[-1] if said else f'exit {done.returncode}'}); {fix}")
    program = entry_point(folder, script)
    # RULE: a plugin starts only from inside its own environment
    if not program.resolve().is_relative_to((folder / ".venv").resolve()):
        return None
    # RULE: a plugin without its entry point after a sync is refused
    if not program.is_file():
        raise Refused(f"{folder / '.venv'} has no {script} after a sync; {fix}")
    return str(program), list(spec.args[2:])
