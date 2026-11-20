"""The owner's own plugins, as the bridge lists them in known_plugins.toml: each one's
repository, and the folder its checkout had before a rename. Adding a plugin here is a
reviewed and merged change to claude-voice, which is why install, the GitHub check and the
voice approval trust this list and never a name a checkout gives."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

KNOWN = Path(__file__).with_name("known_plugins.toml")


@dataclass(frozen=True)
class Known:
    """One of the owner's own plugins: its repository, and the folder its checkout had
    before the repository was renamed, if it was."""

    repo: str
    formerly: str | None = None

    @property
    def folder(self) -> str:
        return self.repo.split("/")[1]


def known() -> dict[str, Known]:
    """The owner's own plugins, by name."""
    raw = tomllib.loads(KNOWN.read_text(encoding="utf-8"))
    return {name: Known(str(body["repo"]), body.get("formerly")) for name, body in raw.items()}


def known_folders() -> set[str]:
    """The config folders the known plugins use: each one's name and its repository's,
    old and new."""
    return {*known(), *(k.folder for k in known().values()), *(k.formerly for k in known().values() if k.formerly)}
