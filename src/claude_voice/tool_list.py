"""A plugin's tools list in the settings file: which tools are new to offer, and the write.

`claude-voice plugins approve` reads a plugin's full listing and shows the owner each tool not
yet in the list, in full. A tool is added only when the owner types `<name> +<n>`, with
`+write` when any of them can change things. The plain name stays a re-approval that never
adds, since it is typed out of habit (design approve-new-tools v1.1).

The plugin's own words are shown with every control character escaped, so nothing it says can
hide or overwrite the screen. The write changes only `[plugins.<name>].tools`, under the lock
every writer of the file takes. It aborts when the file changed while the owner read, and is
put back when anything else changed.
"""

from __future__ import annotations

import hashlib
import os
import re
import stat
import tomllib
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import tomlkit

from . import safefile
from .config import PluginSpec

#: A tool name that can go into the settings file as it is.
PLAIN = re.compile(r"[a-z][a-z0-9_]{0,63}")
#: How many backups of the settings file an approval keeps.
KEEP_BACKUPS = 5
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_ALLOWED = "\n\t"  # a description may run over several lines


class Changed(RuntimeError):
    """The settings file changed while the owner was approving; nothing was written."""


class Mismatch(ValueError):
    """A write that would change more than the tools list; the message says what to do."""


def shown(text: str) -> str:
    """The plugin's own text as it is safe to print: every control character but a line break
    and a tab shown as \\xNN, so an escape sequence cannot hide or overwrite anything."""
    # RULE: plugin text is shown with every control character escaped
    return _CONTROL.sub(lambda m: m.group() if m.group() in _ALLOWED else f"\\x{ord(m.group()):02x}", text)


def _refusal(tool: str, entry: dict[str, Any]) -> str | None:
    """Why a tool is never offered for adding, or None."""
    held = [c for c in _CONTROL.findall(str(entry.get("description") or "")) if c not in _ALLOWED]
    # RULE: a tool whose name or description holds a control character is refused
    if not PLAIN.fullmatch(tool):
        return "not a plain tool name"
    if held:
        return "its description holds a control character"
    return None


@dataclass
class Offer:
    """What approving would change in a plugin's tools list."""

    new: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    gone: list[str] = field(default_factory=list)
    refused: list[tuple[str, str]] = field(default_factory=list)

    def writes(self) -> bool:
        """Whether any new tool can change things: one the plugin does not mark as only reading."""
        # RULE: a new write tool needs plus write
        return any(not entry.get("read_only") for _, entry in self.new)

    def answer(self, name: str) -> str | None:
        """The line that adds the new tools, or None when there are none."""
        if not self.new:
            return None
        return f"{name} +{len(self.new)}" + (" +write" if self.writes() else "")


def offer(spec: PluginSpec, listing: dict[str, dict[str, Any]]) -> Offer:
    """The plugin's tools set against its list: new ones to offer, refused ones, and ones gone."""
    found = Offer(gone=[tool for tool in spec.tools if tool not in listing])
    for tool, entry in listing.items():
        if tool in spec.tools:
            continue
        why = _refusal(tool, entry)
        if why:
            found.refused.append((tool, why))
        else:
            found.new.append((tool, entry))
    return found


def judge(typed: str, name: str, found: Offer) -> str:
    """What the owner's line means: "plain", "add", "wrong" (a count or mark that does not
    match the screen) or "stop"."""
    words = typed.split()
    # RULE: the plain name approves and adds nothing
    if words == [name]:
        return "plain"
    if len(words) < 2 or words[0] != name or not words[1].startswith("+"):
        return "stop"
    # RULE: a wrong count is refused and the screen is shown again
    return "add" if " ".join(words) == found.answer(name) else "wrong"


def listed(text: bytes, plugin: str) -> list[str] | None:
    """The plugin's tools list in the settings file's `text`, or None when it has none."""
    try:
        table = tomllib.loads(text.decode("utf-8")).get("plugins", {}).get(plugin)
    except (UnicodeDecodeError, tomllib.TOMLDecodeError):
        return None
    tools = table.get("tools") if isinstance(table, dict) else None
    return [str(tool) for tool in tools] if isinstance(tools, list) else None


def write_tools(
    path: Path, plugin: str, tools: list[str], seen: bytes | None = None, now: datetime | None = None
) -> None:
    """Set `[plugins.<plugin>].tools` to `tools` in the settings file at `path`, keeping every
    comment and everything else, behind a backup; raise Changed or Mismatch otherwise. `seen` is
    the file as it was when `tools` was worked out from it; a file that differs is not written."""
    bad = [tool for tool in tools if not PLAIN.fullmatch(tool)]
    # RULE: a tool name that is not plain is refused with nothing written
    if bad:
        raise Mismatch(f"{bad[0]!r} is not a plain tool name, so nothing was written")
    real = safefile.target(path)  # a symlinked file is written at its target; the link stays
    with safefile.locked(real):
        before = real.read_bytes()
        # RULE: a change while the owner reads the screen writes nothing
        if seen is not None and before != seen:
            raise Changed(f"{real} changed while approving; nothing was written; run it again")
        text = _with_tools(before, plugin, tools)
        # RULE: a change between the read and the rename writes nothing
        if hashlib.sha256(real.read_bytes()).digest() != hashlib.sha256(before).digest():
            raise Changed(f"{real} changed while approving; nothing was written; run it again")
        mode = _mode(real)
        spare = safefile.backup(real, now or datetime.now())
        _prune(real)
        safefile.replace(real, text, mode)
        _check(real, before, plugin, tools, mode, spare)


def _mode(real: Path) -> int:
    """The file's own permissions, kept through the write."""
    # RULE: the settings file keeps its mode
    return stat.S_IMODE(real.stat().st_mode)


def _with_tools(before: bytes, plugin: str, tools: list[str]) -> str:
    """The file's text with only the plugin's tools list changed, its comments and order kept."""
    doc = tomlkit.parse(before.decode("utf-8"))
    table = doc.get("plugins", {}).get(plugin)
    if table is None:
        raise Mismatch(f"there is no [plugins.{plugin}] in the settings file, so nothing was written")
    if "tools" not in table:
        table["tools"] = tools
        return tomlkit.dumps(doc)
    listed = table["tools"]
    for i in reversed(range(len(listed))):
        if str(listed[i]) not in tools:
            del listed[i]
    for tool in tools:
        if tool not in [str(each) for each in listed]:
            listed.append(tool)
    return tomlkit.dumps(doc)


def _prune(real: Path) -> None:
    """Keep only the newest backups of the settings file."""
    backups = sorted(real.parent.glob(f"{real.name}.*.bak"), key=lambda p: (p.stat().st_mtime_ns, p.name))
    # RULE: a sixth backup removes the oldest
    for old in backups[:-KEEP_BACKUPS]:
        os.unlink(old)


def _check(real: Path, before: bytes, plugin: str, tools: list[str], mode: int, spare: Path) -> None:
    """Read the written file back: it must equal the old one with only the tools list changed."""
    expected = tomllib.loads(before.decode("utf-8"))
    expected["plugins"][plugin]["tools"] = tools
    try:
        written: dict[str, Any] | None = tomllib.loads(real.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError:
        written = None
    # RULE: a write whose re parse differs puts the backup back
    if written != expected:
        safefile.replace(real, before, mode)
        raise Mismatch(
            f"writing the tools list would have changed more of {real}, so it was put back from {spare.name} "
            "and nothing was approved. Add the tools to its list by hand, then run plugins approve again"
        )
