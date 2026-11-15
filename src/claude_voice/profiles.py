"""Profiles: which repositories and plugins a developer gets, in layers (docs/setup.md).

A profile is a TOML file with `[[repo]]` entries, `[[plugin]]` entries whose `[plugins.<name>]`
block and config files are templates, and an optional `remove = [names]`. The layers are the
default shipped here, the user's own (`profile = ...` in config.toml), and this machine's
`profile.local.toml`. A later layer adds an entry, or replaces or removes one by name.

A profile is shared as a plain file, so `problems` is an allow-list rather than a search for
known secrets. Templates name the user's values as `{placeholder}`s, and those values live
only in the user's own `values.toml`.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import UPSTREAM, UPSTREAM_MOBILE, UPSTREAM_UI

DEFAULT = Path(__file__).parent / "default_profile.toml"
LOCAL = ".config/claude-voice/profile.local.toml"  # under the user's home: this machine's layer
PUBLIC_REPO = re.compile(r"https://github\.com/([A-Za-z0-9-]+)/([A-Za-z0-9._-]+?)(?:\.git)?")
#: The project's own repositories. Its account's name is public in these three addresses,
#: and a profile may name that account nowhere else.
UPSTREAM_URLS = tuple(f"https://github.com/{repo}" for repo in (UPSTREAM, UPSTREAM_UI, UPSTREAM_MOBILE))
UPSTREAM_OWNER = UPSTREAM.split("/")[0].lower()
PLACEHOLDER = re.compile(r"\{([a-z][a-z0-9_]*)\}")
WORD = re.compile(r"[a-z0-9]+")
KINDS = ("core", "app", "plugin")
TOP = {"repo", "plugin", "remove"}
CONFIG_HOME = "~/.config/"

#: The words a template may use besides placeholders and the names of the profile's own
#: repositories. Each one is generic: a word that could only be someone's own never goes here.
SAFE_WORDS = frozenset(
    """
    plugins plugin command args cwd tools tool timeout branch main master serve stdio http https
    venv bin src lib dir config toml json yaml env home lat lon contact url api log level
    mcp python python3 uv run node npm status start stop now today forecast list get set
    read search info debug true false on off yes no metric imperial celsius
    """.split()
)
#: The numbers a template may hold: documented defaults, never a device's or a place's.
NUMBERS = frozenset({0, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 3600})
#: What a comment must never hold, as a second layer behind the allow-list.
PRIVATE = (
    re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b"),  # an IPv4 address
    re.compile(r"\b[0-9a-f]{1,4}(?::[0-9a-f]{0,4}){3,7}\b", re.I),  # an IPv6 or MAC address
    re.compile(r"-?\b\d{1,3}\.\d{3,}\b"),  # a coordinate
    re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"),  # an email address
    re.compile(r"\b[\w-]+\.(?:local|lan|home|internal)\b(?!\.)", re.I),  # a host on a home network
    re.compile(r"\d{6,}"),  # a serial, charger or phone number
    re.compile(r"\b(?:sk-|ghp_|gho_|github_pat_|xox[abp]-)\w+"),  # a key or a token
)


class ProfileError(ValueError):
    pass


@dataclass(frozen=True)
class Repo:
    name: str  # the folder it is cloned into
    url: str
    kind: str = "plugin"
    optional: bool = False


@dataclass(frozen=True)
class Plugin:
    name: str
    repo: str
    block: str = ""
    config: dict[str, str] = field(default_factory=dict)


@dataclass
class Profile:
    repos: dict[str, Repo] = field(default_factory=dict)
    plugins: dict[str, Plugin] = field(default_factory=dict)


def layers(user: Path | None, home: Path) -> list[Path]:
    """The profile files, in the order they are laid over each other."""
    return [DEFAULT, *([user] if user else []), home / LOCAL]


def parse(text: str, where: str) -> dict[str, Any]:
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ProfileError(f"{where} does not parse as TOML ({exc}). Fix it, or move it away.") from exc


def load(paths: list[Path]) -> Profile:
    """The layers that exist, read and merged; a broken one names its file and the fix.

    Whether each plugin's repository is listed is a question about the merged profile: a
    user's plugin may come from a repository the default lists, and a later layer may
    remove a repository a plugin still needs.
    """
    found = [p for p in paths if p.is_file()]
    profile = merge([parse(p.read_text(encoding="utf-8"), str(p)) for p in found])
    named = ", ".join(str(p) for p in found)
    for plugin in dangling(profile):
        raise ProfileError(
            f"plugin {plugin.name!r} needs the repo {plugin.repo!r}, which none of the layers lists once "
            f"merged ({named}). Add a [[repo]] named {plugin.repo!r} to one of them, or remove the plugin too."
        )
    return profile


def dangling(profile: Profile) -> list[Plugin]:
    """The plugins whose repository the merged profile does not list."""
    # RULE: a profile plugin comes from a repo the merged profile lists
    return [p for p in profile.plugins.values() if p.repo not in profile.repos]


def _repo(raw: dict[str, Any]) -> Repo:
    url = str(raw.get("url", ""))
    name = str(raw.get("name") or url.rstrip("/").rsplit("/", 1)[-1].removesuffix(".git"))
    return Repo(name, url, str(raw.get("kind", "plugin")), bool(raw.get("optional", False)))


def _plugin(raw: dict[str, Any]) -> Plugin:
    config = {str(k): str(v) for k, v in (raw.get("config") or {}).items()}
    return Plugin(str(raw.get("name", "")), str(raw.get("repo", "")), str(raw.get("block", "")), config)


def merge(raws: list[dict[str, Any]]) -> Profile:
    """Each layer over the ones before it, by name."""
    repos: dict[str, Repo] = {}
    plugins: dict[str, Plugin] = {}
    for raw in raws:
        for name in raw.get("remove") or []:
            for table in (repos, plugins):
                # RULE: a profile layer can remove an entry by name
                table.pop(str(name), None)
        for entry in map(_repo, raw.get("repo") or []):
            # RULE: a later profile layer replaces an entry by name
            repos[entry.name] = entry
        for plugin in map(_plugin, raw.get("plugin") or []):
            plugins[plugin.name] = plugin
    return Profile(repos, plugins)


# -- the guard ---------------------------------------------------------------------------


def private_words(text: str) -> list[str]:
    """What in `text` looks like someone's own address, place, number or key."""
    return [m.group(0) for pattern in PRIVATE for m in pattern.finditer(text)]


def _comments(text: str) -> list[str]:
    found = []
    for number, line in enumerate(text.splitlines(), 1):
        if line.lstrip().startswith("#"):
            # RULE: a private word in a profile comment is refused
            for word in private_words(line):
                found.append(f"line {number}: the comment holds {word!r}, which looks private; take it out")
        # RULE: a profile comment starts its line
        elif "#" in line:
            found.append(f"line {number}: a comment must start its line, so the private-word scan can read it")
    return found


def _words(text: str, allowed: frozenset[str], where: str) -> list[str]:
    bad = [w for w in WORD.findall(text.lower()) if w not in allowed]
    # RULE: every word in a profile is a placeholder or a safe word
    if bad:
        return [f"{where}: {', '.join(dict.fromkeys(bad))} is not a placeholder or a safe word; use a {{placeholder}}"]
    return []


def _value(value: Any, allowed: frozenset[str], where: str) -> list[str]:
    """Every key, table name, string and number in a parsed template, against the allow-list."""
    if isinstance(value, dict):
        found = []
        for key, inner in value.items():
            found += _words(str(key), allowed, where) + _value(inner, allowed, f"{where}.{key}")
        return found
    if isinstance(value, list):
        return [p for item in value for p in _value(item, allowed, where)]
    if isinstance(value, bool):
        return []
    if isinstance(value, int | float):
        # RULE: a number in a profile is a documented default
        known = isinstance(value, int) and value in NUMBERS
        return [] if known else [f"{where}: {value} is not a documented default; use a {{placeholder}}"]
    return _words(str(value), allowed, where)


def _template(text: str, allowed: frozenset[str], where: str) -> tuple[list[str], dict[str, Any]]:
    """A template's placeholders checked by name, then the rest parsed with each one blanked."""
    found = [p for name in PLACEHOLDER.findall(text) for p in _words(name, allowed, f"{where} {{{name}}}")]
    try:
        parsed = tomllib.loads(PLACEHOLDER.sub(" ", text))
    except tomllib.TOMLDecodeError as exc:
        return [*found, f"{where} does not parse as TOML with its placeholders filled in ({exc})"], {}
    return found + _value(parsed, allowed, where), parsed


def _repo_problems(raw: dict[str, Any]) -> list[str]:
    url, repo = str(raw.get("url", "")), _repo(raw)
    match = PUBLIC_REPO.fullmatch(url)
    # RULE: a profile repo is a url on the public host
    if not match:
        return [f"repo {url!r} is not a repository on https://github.com; a profile names only public ones"]
    found = [] if repo.name == match.group(2) else [f"repo {repo.name!r} must be named as its URL names it"]
    # RULE: the upstream account appears only in its three repositories
    if match.group(1).lower() == UPSTREAM_OWNER and url.removesuffix(".git") not in UPSTREAM_URLS:
        found.append(f"repo {url!r}: of the upstream account, a profile may name only {', '.join(UPSTREAM_URLS)}")
    if repo.kind not in KINDS:
        found.append(f"repo {repo.name!r}: kind {repo.kind!r} is not one of {', '.join(KINDS)}")
    return found


def _plugin_problems(plugin: Plugin, allowed: frozenset[str]) -> list[str]:
    where = f"plugin {plugin.name!r}"
    found = _words(plugin.name, allowed, where)
    problems, block = _template(plugin.block, allowed, f"{where} block")
    found += problems
    # RULE: a plugin block sets only its own plugin
    if set(block) != {"plugins"} or set(block["plugins"]) != {plugin.name}:
        found.append(f"{where}: its block must set only [plugins.{plugin.name}]")
    for path, text in plugin.config.items():
        if not path.startswith(CONFIG_HOME):
            found.append(f"{where}: {path} is not under {CONFIG_HOME}")
        found += _words(path, allowed, f"{where} config path") + _template(text, allowed, f"{where} {path}")[0]
    return found


def problems(text: str, known: tuple[str, ...] = ()) -> list[str]:
    """Everything in a profile's text that must not be shared; an empty list means it may be.

    It checks one file, for what it holds. `known` names the repositories earlier layers list,
    whose names a plugin here may use; whether each plugin's repository is listed at all is
    asked of the merged profile (`dangling`).
    """
    found = _comments(text)
    try:
        raw = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        return [*found, f"the profile does not parse as TOML ({exc})"]
    found += [f"unknown key {key!r}; a profile holds only {', '.join(sorted(TOP))}" for key in sorted(set(raw) - TOP)]
    repos = [_repo(r) for r in raw.get("repo") or []]
    for entry in raw.get("repo") or []:
        found += _repo_problems(entry)
    names = [*known, *(r.name for r in repos)]
    allowed = SAFE_WORDS | {w for name in names for w in WORD.findall(name.lower())}
    for plugin in map(_plugin, raw.get("plugin") or []):
        found += _plugin_problems(plugin, allowed)
    return found
