"""Where the project's repositories are: on GitHub, and as clones on this computer.

One file, ``~/.config/claude-voice/config.toml`` (or CLAUDE_VOICE_CONFIG), so someone
else can install the bridge, or contribute to it, with their clones wherever they keep
them and from a fork. Every setting has a default, worked out from the clone the bridge
runs from, so the file is only needed to change something:

    [repos]
    claude_voice = "roykollensvendsen/claude-voice"        # the project, on GitHub
    claude_voice_ui = "roykollensvendsen/claude-voice-ui"
    fork = "you/claude-voice"                               # your fork, if you have one

    [paths]
    claude_voice = "~/src/claude-voice"         # default: the clone the bridge runs from
    claude_voice_ui = "~/src/claude-voice-ui"   # default: next to it
    ui_dist = "~/src/claude-voice-ui/dist"      # default: inside it; CLAUDE_VOICE_UI_DIR wins
    contribute = "~/src/claude-voice"           # the contribute session's clone; default: claude_voice

    [sessions]
    remote_control = true                       # a session the bridge starts is reachable from the Claude app
    launch = "terminal"                         # or "background": where a started session runs
    terminal_command = "kitty --directory {cwd}"  # default: the first of gnome-terminal, kgx, konsole, xterm

    [budgets]                                   # seconds each client is answered within
    claude_app = 20                             # the Claude app drops a call after about 30 s
    voice_app = 120
    default = 60
"""

from __future__ import annotations

import os
import re
import subprocess
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

FILE = Path("~/.config/claude-voice/config.toml")
REPO = re.compile(r"[A-Za-z0-9-]+/[A-Za-z0-9._-]+")
UPSTREAM = "roykollensvendsen/claude-voice"
UPSTREAM_UI = "roykollensvendsen/claude-voice-ui"
LAUNCHES = ("terminal", "background")


class ConfigError(ValueError):
    pass


@dataclass
class Repos:
    claude_voice: str = UPSTREAM
    claude_voice_ui: str = UPSTREAM_UI
    fork: str | None = None


@dataclass
class Paths:
    claude_voice: Path | None
    claude_voice_ui: Path | None
    ui_dist: Path | None
    contribute: Path | None


@dataclass
class Sessions:
    remote_control: bool = True
    launch: str = "terminal"
    terminal_command: str | None = None


BUDGETS = {"claude_app": 20.0, "voice_app": 120.0, "default": 60.0}


class Budgets:
    """How long each client is answered within, in seconds: the Claude app drops a tool call
    after about 30 seconds, the voice app waits longer. Slow work goes on in the background."""

    def __init__(self, seconds: Mapping[str, float] | None = None) -> None:
        self._seconds = {**BUDGETS, **(seconds or {})}

    def seconds(self, client: str) -> float:
        return self._seconds.get(client, self._seconds["default"])


def _budgets(raw: dict[str, Any] | None) -> Budgets:
    raw = raw or {}
    for key, value in raw.items():
        # RULE: a time budget that is not a positive number is refused
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
            raise ConfigError(f"[budgets] {key} = {value!r} is not a positive number of seconds")
    return Budgets({k: float(v) for k, v in raw.items()})


@dataclass
class Config:
    repos: Repos
    paths: Paths
    file: Path | None
    sessions: Sessions = field(default_factory=Sessions)
    budgets: Budgets = field(default_factory=Budgets)


def _repo(value: Any, key: str) -> str:
    # RULE: a repository that is not owner slash name is refused
    if not isinstance(value, str) or not REPO.fullmatch(value) or ".." in value:
        raise ConfigError(f"[repos] {key} = {value!r} is not owner/name, such as roykollensvendsen/claude-voice")
    return value


def _path(value: Any, home: str) -> Path | None:
    if not value:
        return None
    text = str(value)
    if text.startswith("~"):
        text = home + text[1:]
    return Path(text)


def load(path: Path | None = FILE, own_clone: Path | None = None, env: Mapping[str, str] = os.environ) -> Config:
    """The settings: the file laid over the defaults."""
    home = env.get("HOME") or str(Path.home())
    raw: dict[str, Any] = {}
    file = _path(env.get("CLAUDE_VOICE_CONFIG"), home) or (_path(str(path), home) if path else None)
    if file is not None and file.is_file():
        with file.open("rb") as f:
            raw = tomllib.load(f)
    repos_raw, paths_raw = raw.get("repos") or {}, raw.get("paths") or {}
    repos = Repos(
        claude_voice=_repo(repos_raw.get("claude_voice", UPSTREAM), "claude_voice"),
        claude_voice_ui=_repo(repos_raw.get("claude_voice_ui", UPSTREAM_UI), "claude_voice_ui"),
        fork=_repo(repos_raw["fork"], "fork") if repos_raw.get("fork") else None,
    )
    voice = _path(paths_raw.get("claude_voice"), home) or own_clone
    ui = _path(paths_raw.get("claude_voice_ui"), home) or (voice.parent / "claude-voice-ui" if voice else None)
    dist = (
        _path(env.get("CLAUDE_VOICE_UI_DIR"), home)
        or _path(paths_raw.get("ui_dist"), home)
        or (ui / "dist" if ui else None)
    )
    contribute = _path(paths_raw.get("contribute"), home) or voice
    return Config(
        repos,
        Paths(voice, ui, dist, contribute),
        file,
        _sessions(raw.get("sessions") or {}),
        _budgets(raw.get("budgets")),
    )


def _sessions(raw: dict[str, Any]) -> Sessions:
    launch = raw.get("launch", "terminal")
    # RULE: a way of starting a session that does not exist is refused
    if launch not in LAUNCHES:
        raise ConfigError(f"[sessions] launch = {launch!r} is not one of: {', '.join(LAUNCHES)}")
    remote = raw.get("remote_control", True)
    if not isinstance(remote, bool):
        raise ConfigError(f"[sessions] remote_control = {remote!r} is not true or false")
    command = raw.get("terminal_command")
    return Sessions(remote, launch, str(command) if command else None)


def _remotes(folder: Path) -> list[str]:
    from . import procs

    git = procs.find("git") or "git"
    try:
        out = subprocess.run(
            [git, "-C", str(folder), "remote", "-v"], capture_output=True, text=True, timeout=10, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return []
    return [line.split()[1] for line in out.stdout.splitlines() if len(line.split()) >= 2]


def _names(url: str, repo: str) -> bool:
    tail = re.sub(r"\.git/?$", "", url.strip()).lower()
    return tail.endswith("/" + repo.lower()) or tail.endswith(":" + repo.lower())


def _clone_check(
    id: str, title: str, folder: Path | None, wanted: list[str], where: str, affects: str
) -> dict[str, Any]:
    from .doctor import check

    fix = f"Clone it with `git clone https://github.com/{wanted[0]} <folder>`, then set [paths] {where} in {FILE}"
    if folder is None:
        return check(id, title, False, "no folder is set and none could be worked out", affects=affects, fix=fix)
    if not folder.is_dir():
        return check(id, title, False, f"{folder} does not exist", affects=affects, fix=fix)
    if not (folder / ".git").exists():
        return check(id, title, False, f"{folder} is not a git clone", affects=affects, fix=fix)
    remotes = _remotes(folder)
    if not any(_names(url, repo) for url in remotes for repo in wanted):
        seen = ", ".join(sorted({re.sub(r"^.*[:/]([^/:]+/[^/]+?)(\.git)?/?$", r"\1", u) for u in remotes})) or "none"
        return check(
            id,
            title,
            False,
            f"{folder} points at {seen}, not {' or '.join(wanted)}",
            affects=affects,
            fix=f"Point it at the right repository, or set [repos] (and fork) in {FILE}",
        )
    return check(id, title, True, f"{folder} is a clone of {' or '.join(wanted)}", affects=affects)


def check_paths(cfg: Config) -> list[dict[str, Any]]:
    """For the doctor: does each folder exist, is it a clone, and of the right repository."""
    from .doctor import check

    voice = [cfg.repos.claude_voice, *([cfg.repos.fork] if cfg.repos.fork else [])]
    dist = cfg.paths.ui_dist
    built = dist is not None and (dist / "index.html").is_file()
    return [
        _clone_check(
            "path_claude_voice",
            "The claude-voice clone",
            cfg.paths.claude_voice,
            voice,
            "claude_voice",
            "updating the bridge",
        ),
        _clone_check(
            "path_claude_voice_ui",
            "The claude-voice-ui clone",
            cfg.paths.claude_voice_ui,
            [cfg.repos.claude_voice_ui],
            "claude_voice_ui",
            "building the companion app",
        ),
        check(
            "path_ui_dist",
            "The companion app's built files",
            built,
            f"{dist} holds the built app" if built else f"{dist} has no built app (no index.html)",
            affects="the companion app on the phone",
            fix=f"Build it in {cfg.paths.claude_voice_ui} (its README says how), or set [paths] ui_dist in {FILE}",
        ),
        _clone_check(
            "path_contribute",
            "The contribute session's clone",
            cfg.paths.contribute,
            voice,
            "contribute",
            "the contribute session",
        ),
    ]
