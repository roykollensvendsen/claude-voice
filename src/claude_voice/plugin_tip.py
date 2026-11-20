"""The plugin's code as GitHub has it: a voice approval needs the checkout at its branch's tip
there, not only level with what the checkout last fetched (plugin-config-commands-v1, b3/f1).

The cheap check, `plugins.checkout_refusal`, trusts the checkout's own record of its remote,
which a session could move. This one asks GitHub, for the repository named in the bridge's own
list of known plugins, never for one the checkout names.
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Callable
from pathlib import Path

from . import procs, safe_git
from .config import PluginSpec
from .known_plugins import known

SHA = re.compile(r"[0-9a-f]{40}")
SECONDS = 10.0  # a voice approval waits no longer than this for GitHub


def github_tip(repo: str, branch: str) -> str | None:
    """The commit at the tip of `branch` on GitHub, or None when it cannot be read."""
    gh = procs.find("gh")
    if not gh:
        return None
    try:
        out = subprocess.run(
            [gh, "api", f"repos/{repo}/branches/{branch}", "--jq", ".commit.sha"],
            capture_output=True,
            text=True,
            timeout=SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    sha = out.stdout.strip()
    return sha if out.returncode == 0 and SHA.fullmatch(sha) else None


def head(folder: str) -> str | None:
    """The commit the checkout has out, in full, or None."""
    try:
        out = safe_git.run(
            "-C", str(Path(folder)), "rev-parse", "HEAD", capture_output=True, text=True, timeout=10, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() if out.returncode == 0 else None


def refusal(
    spec: PluginSpec,
    *,
    tip_of: Callable[[str, str], str | None] = github_tip,
    head_of: Callable[[str], str | None] = head,
) -> str | None:
    """Why the plugin may not be approved by voice, as GitHub sees it, or None when its checkout
    is at the tip of the branch it follows."""
    found = known().get(spec.name)
    # RULE: the repository comes from the bridges own list of plugins
    if found is None:
        return f"{spec.name} is not one of the known plugins, so its code cannot be checked on GitHub"
    tip = tip_of(found.repo, spec.branch)
    # RULE: a tip that cannot be read means typed
    if tip is None:
        return "its tip on GitHub could not be read, so type it instead"
    # RULE: a forged tracking ref is refused since head is not the tip on github
    if head_of(spec.cwd) != tip:
        return f"its checkout is not the tip of {spec.branch} on GitHub ({found.repo})"
    return None
