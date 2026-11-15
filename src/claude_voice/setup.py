"""`claude-voice setup --check`: every step of setting up the platform, in the order it is done.

Each step is done, missing, or manual: a step only the person can take, such as signing in,
which is given to them as the exact thing to run. The check only reads, so it can be run at
any time, on any machine, and changes nothing (docs/setup.md).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import plugins
from .config import FILE, Config
from .profiles import Profile
from .reposync import Folder

DONE, MISSING, MANUAL = "done", "missing", "manual"
#: The programs the platform needs, each with what it is for and how to get it.
TOOLS = (
    ("uv", "uv", "uv, which installs Python tools", "Install it: `curl -LsSf https://astral.sh/uv/install.sh | sh`"),
    ("node", "node", "Node.js, which builds the companion app", "Install Node.js 20 or newer from https://nodejs.org"),
    ("claude_code", "claude", "Claude Code", "Install it: `curl -fsSL https://claude.ai/install.sh | bash`"),
    (
        "bridge",
        "claude-voice",
        "the claude-voice command",
        "Install it: `uv tool install git+https://github.com/roykollensvendsen/claude-voice`",
    ),
)
UNIT = ".config/systemd/user/claude-voice.service"  # under the home, as deploy/install.sh writes it
PUBLIC_KEYS = ("CLAUDE_VOICE_PUBLIC_HOSTS", "CLAUDE_VOICE_PUBLIC_URL")
PHONE = (
    "Reaching the bridge from a phone is yours to set up: sign in to Tailscale, turn on Funnel in its "
    "admin console, run `tailscale funnel --bg 8811`, then set CLAUDE_VOICE_PUBLIC_HOSTS to the "
    'address it prints (README, "Run it as a service and reach it from the phone"). If the service\'s '
    "env file already sets CLAUDE_VOICE_PUBLIC_HOSTS, this is done: check that file yourself. Setup "
    "never opens it, because it holds the login secret."
)


@dataclass
class Step:
    name: str
    status: str
    detail: str
    fix: str = ""
    optional: bool = False


def _yours(name: str, done: bool, detail: str, fix: str) -> Step:
    """A step only the person can take: done, or theirs to run, never one setup would do."""
    # RULE: a step only a person can take is theirs to run
    return Step(name, DONE, detail) if done else Step(name, MANUAL, detail, fix)


def _have(name: str, done: bool, detail: str, fix: str, optional: bool = False) -> Step:
    return Step(name, DONE, detail, optional=optional) if done else Step(name, MISSING, detail, fix, optional)


def _tools(which: Callable[[str], str | None]) -> list[Step]:
    found = []
    for name, program, what, fix in TOOLS:
        where = which(program)
        found.append(_have(name, bool(where), f"{what} is at {where}" if where else f"{what} was not found", fix))
    return found


def _bridge(found: list[dict[str, Any]]) -> list[Step]:
    """The bridge's own check, split into what setup can see to and the sign-in, which is the person's."""
    failing = [c for c in found if c["required"] and not c["ok"] and c["name"] != "signed_in"]
    first = failing[0] if failing else None
    login = next((c for c in found if c["name"] == "signed_in"), None)
    steps = [
        _have(
            "bridge_check",
            first is None,
            first["detail"] if first else "`claude-voice check` passes, apart from signing in",
            first["fix"] if first else "",
        )
    ]
    if login is not None:
        steps.append(_yours("signed_in", login["ok"], login["detail"], login["fix"]))
    return steps


def _service_installed(home: Path) -> tuple[bool, Path]:
    """Whether the bridge is installed as a service, and where its unit file is.

    The one place setup names the service manager, for the platform layer to take over.
    """
    unit = home / UNIT
    return unit.is_file(), unit


def _machine(cfg: Config, env: Mapping[str, str], home: Path) -> list[Step]:
    config = cfg.file if cfg.file is not None else FILE
    installed, unit = _service_installed(home)
    dist = cfg.paths.ui_dist
    built = dist is not None and (dist / "index.html").is_file()
    public = any(env.get(k) for k in PUBLIC_KEYS)  # the environment only: setup never opens the env file
    return [
        _have(
            "config",
            config.is_file(),
            f"the settings are in {config}" if config.is_file() else f"{config} does not exist yet",
            f'Write it from the example in the README\'s "Settings", with your own values, at {config}',
        ),
        _have(
            "service",
            installed,
            f"the service is installed at {unit}" if installed else "the bridge is not installed as a service",
            'Run `deploy/install.sh` in your clone of claude-voice (README, "Run it as a service")',
        ),
        _have(
            "ui_build",
            built,
            f"{dist} holds the built companion app" if built else f"{dist} has no built companion app",
            f"Build it in {cfg.paths.claude_voice_ui} (its README says how), or set [paths] ui_dist in {config}",
        ),
        _yours("phone_route", public, "a public address is set" if public else "no public address is set", PHONE),
    ]


def _repos(folders: Iterable[Folder]) -> list[Step]:
    found = []
    for f in folders:
        fix = f"Clone it: `git clone {f.url} {f.path}`"
        if f.state == "not a clone":
            fix = f"Move {f.path} away, then clone it: `git clone {f.url} {f.path}`"
        found.append(_have(f"repo:{f.name}", f.state in ("clone", "other origin"), f.line(), fix, f.optional))
        has_hooks, hooks_path = f.hooks
        if has_hooks:
            # RULE: a checkout without the commit hook is flagged
            on = hooks_path == ".githooks"
            detail = "its commit-msg hook is on" if on else "its commit-msg hook is off, so CI is the first to refuse"
            fix = f"Turn it on: `cd {f.path} && git config core.hooksPath .githooks`"
            found.append(_have(f"hooks:{f.name}", on, detail, fix))
    return found


def _plugins(profile: Profile, cfg: Config, approved: Callable[[Any], bool]) -> list[Step]:
    specs = {s.name: s for s in cfg.plugins}
    found = []
    for plugin in profile.plugins.values():
        spec = specs.get(plugin.name)
        if spec is None:
            fix = f"Add its block to {cfg.file or FILE}, with your own values:\n{plugin.block.strip()}"
            found.append(_have(f"plugin:{plugin.name}", False, f"{plugin.name} is not in the settings", fix, True))
            continue
        done = approved(spec)
        detail = f"{plugin.name} is approved" if done else f"{plugin.name} is set up and not approved yet"
        found.append(_yours(f"plugin:{plugin.name}", done, detail, f"Run: claude-voice plugins approve {plugin.name}"))
    return found


def steps(
    cfg: Config,
    env: Mapping[str, str],
    home: Path,
    bridge: list[dict[str, Any]],
    profile: Profile,
    folders: list[Folder],
    which: Callable[[str], str | None],
    approved: Callable[[Any], bool] = plugins.approved,
) -> list[Step]:
    """Every step, in the order a person takes them."""
    machine = _machine(cfg, env, home)
    return [
        *_tools(which),
        machine[0],
        *_bridge(bridge),
        *machine[1:],
        *_repos(folders),
        *_plugins(profile, cfg, approved),
    ]


def missing(found: Iterable[Step]) -> list[Step]:
    """The steps still to do before setup is complete; an optional one does not count."""
    # RULE: an optional step missing does not fail the check
    return [s for s in found if s.status == MISSING and not s.optional]


def lines(found: list[Step]) -> list[str]:
    """The steps for a person: a count first, then one line each."""
    count = {status: sum(s.status == status for s in found) for status in (DONE, MISSING, MANUAL)}
    head = (
        f"claude-voice setup --check: {count[DONE]} done, {count[MISSING]} missing, "
        f"{count[MANUAL]} for you to run; nothing was changed"
    )
    body = []
    for s in found:
        said = f"{s.detail}. {s.fix}" if s.fix else s.detail
        body.append(f"{s.status:<8} {s.name}{' (optional)' if s.optional else ''}: {said}")
    return [head, *body]
