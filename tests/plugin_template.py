"""Render the plugin template into a folder, as `claude-voice new-plugin` will.

The template in `templates/plugin/` is a working plugin named `gizmo`, a made-up device, so
its own tests run where it stands. Rendering copies it and swaps the name: `gizmo` for the new
plugin's name, `Gizmo` and `GIZMO` for the same name capitalised, in every path and every text
file.

This lives with the tests until the command exists; the command takes it over then.
"""

from __future__ import annotations

import pathlib
import re
import shutil

ROOT = pathlib.Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "templates" / "plugin"
#: The made-up name the template is written with, and so the token that is swapped.
TOKEN = "gizmo"
#: A name that renders cleanly: it lands in a package name, a folder name and a tool name alike.
#: The longest name, in characters.
LONGEST = 24
NAME = re.compile(rf"[a-z][a-z0-9]{{0,{LONGEST - 1}}}")
#: What a copy of the template never takes along: caches and the files a run leaves.
SKIPPED = {"__pycache__", ".venv", ".pytest_cache", ".ruff_cache", ".mypy_cache", ".coverage"}


def render(name: str, dest: pathlib.Path, template: pathlib.Path = TEMPLATE) -> pathlib.Path:
    """Copy `template` to `dest` with `name` in place of the token; `dest` must not exist yet."""
    if not NAME.fullmatch(name):
        msg = (
            f"{name!r} is not a plugin name: use lower-case letters and digits, starting with a letter,"
            f" {LONGEST} at most"
        )
        raise ValueError(msg)
    if not template.is_dir():
        msg = f"no template at {template}; render from a claude-voice checkout that has templates/plugin/"
        raise FileNotFoundError(msg)
    if dest.exists():
        msg = f"{dest} exists already; pick a folder that does not"
        raise FileExistsError(msg)
    for source in sorted(template.rglob("*")):
        relative = source.relative_to(template)
        if any(part in SKIPPED for part in relative.parts) or source.is_dir():
            continue
        target = dest / _swap(relative.as_posix(), name)
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            text = source.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            shutil.copyfile(source, target)
        else:
            target.write_text(_swap(text, name), encoding="utf-8")
        target.chmod(source.stat().st_mode)
    return dest


def _swap(text: str, name: str) -> str:
    for token, new in ((TOKEN, name), (TOKEN.capitalize(), name.capitalize()), (TOKEN.upper(), name.upper())):
        text = text.replace(token, new)
    return text
