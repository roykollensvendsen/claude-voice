"""What another machine allows now, per plugin: the `access` field of its agent_info.

Each plugin entry may carry `access`, one key per name in its `uses`, each "granted",
"refused" or "never_asked" (next-version.md). The hub reads it on every connection, and
again when the owner asks or after a call to a tool it held as off. Design: access-hub-v1.

It is advice only. The hub warns from it and answers the owner's question, but never skips
or refuses a call because of it: the tool's own answer decides. A key outside `uses` makes
the hub ignore the entry's access, and say so. An unknown value, or a use with no key,
counts as unknown and gives no warning, so a later value breaks nothing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

STATES = ("granted", "refused", "never_asked")
#: The states the voice warns about. The app asks for nothing when it opens, so both get one fix.
OFF = ("refused", "never_asked")
TURN_ON = "turn on {use} for claude-voice in the phone's settings"
LAST = "Last I heard, "


@dataclass
class Held:
    plugins: dict[str, dict[str, str]] = field(default_factory=dict)  # plugin -> use -> state
    tools: dict[str, str] = field(default_factory=dict)  # the machine's tool name -> its plugin
    refused: list[str] = field(default_factory=list)  # entries whose access was ignored, and why

    def off(self, tool: str) -> list[str]:
        """The uses of the tool's plugin last reported as off."""
        states = self.plugins.get(self.tools.get(tool, ""), {})
        return [use for use, state in states.items() if state in OFF]

    def warning(self, machine: str, tool: str, fresh: bool) -> str | None:
        """What the voice is told along with the tool's answer, or None."""
        uses = self.off(tool)
        if not uses:
            return None
        said = _off(machine, self.tools[tool], uses)
        return said if fresh else LAST + said

    def answer(self, machine: str, fresh: bool) -> dict[str, Any]:
        """The answer to "which permissions does <machine> have?"."""
        parts = [_off(machine, name, uses) for name, uses in self._each(OFF)]
        parts += [
            f"{machine} has not said whether {', '.join(uses)} is on for {name}."
            for name, uses in self._each(("unknown",))
        ]
        parts += [f"{machine} sent an access list that was ignored: {why}." for why in self.refused]
        say = " ".join(parts) or f"everything {machine}'s plugins use is on."
        return {"machine": machine, "plugins": self.plugins, "say": say[0].upper() + say[1:] if fresh else LAST + say}

    def _each(self, states: tuple[str, ...]) -> list[tuple[str, list[str]]]:
        found = [(name, [u for u, s in uses.items() if s in states]) for name, uses in self.plugins.items()]
        return [(name, uses) for name, uses in found if uses]


def _off(machine: str, plugin: str, uses: list[str]) -> str:
    fixes = "; ".join(TURN_ON.format(use=use) for use in uses)
    return f"{machine} has {', '.join(uses)} off for {plugin}: {fixes}."


def read(info: Any) -> Held:
    """The access each plugin entry reports. Anything malformed is read as nothing known."""
    held = Held()
    entries = info.get("plugins") if isinstance(info, dict) else None
    for entry in entries if isinstance(entries, list) else ():
        if isinstance(entry, dict) and isinstance(entry.get("name"), str):
            _entry(held, entry)
    return held


def _entry(held: Held, entry: dict[str, Any]) -> None:
    """Add one plugin entry's access to `held`, or the reason it was ignored."""
    name = entry["name"]
    uses = [u for u in entry.get("uses") or () if isinstance(u, str)]
    given = entry.get("access")
    given = given if isinstance(given, dict) else {}
    extra = [key for key in given if key not in uses]
    # RULE: an access key outside uses refuses the entry
    if extra:
        held.refused.append(f"{name}: access names {extra[0]}, which it does not use")
        return
    # RULE: an unknown access value gives no warning
    held.plugins[name] = {use: given[use] if given.get(use) in STATES else "unknown" for use in uses}
    held.tools.update({tool: name for tool in entry.get("tools") or () if isinstance(tool, str)})
