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

import re
from dataclasses import dataclass, field
from typing import Any

STATES = ("granted", "refused", "never_asked")
#: A plugin's name, as protocol v1 allows it; nothing else is spoken.
NAME = re.compile(r"[a-z][a-z0-9_]{0,31}")
#: The private things the protocol names: version 1's, and `messages` from the next version.
USES = (
    "microphone",
    "camera",
    "location",
    "calls",
    "contacts",
    "notifications",
    "media",
    "sensors",
    "usage",
    "messages",
)
#: The states the voice warns about. The app asks for nothing when it opens, so both get one fix.
OFF = ("refused", "never_asked")
TURN_ON = "turn on {use} for claude-voice in the phone's settings"
LAST = "Last I heard, "
NO_ANSWER = "did not answer, so I don't know which permissions it has."


@dataclass
class Held:
    plugins: dict[str, dict[str, str]] = field(default_factory=dict)  # plugin -> use -> state
    tools: dict[str, str] = field(default_factory=dict)  # the machine's tool name -> its plugin
    refused: list[str] = field(default_factory=list)  # entries whose access was ignored, and why
    answered: bool = False  # whether the machine's agent_info was read at all

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
        # RULE: a machine that never answered is not reported as all clear
        if not self.answered:
            return {"machine": machine, "plugins": {}, "say": f"{machine[:1].upper()}{machine[1:]} {NO_ANSWER}"}
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
    held = Held(answered=isinstance(info, dict))
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
    # RULE: only names the protocol allows are ever spoken
    if not NAME.fullmatch(name):
        held.refused.append("an entry with a name the protocol does not allow")
        return
    if any(use not in USES for use in uses):
        held.refused.append(f"{name}: a use the protocol does not name")
        return
    # RULE: an access key outside uses refuses the entry
    if any(key not in uses for key in given):
        held.refused.append(f"{name}: access names a use it does not have")
        return
    # RULE: an unknown access value gives no warning
    held.plugins[name] = {use: given[use] if given.get(use) in STATES else "unknown" for use in uses}
    held.tools.update({tool: name for tool in entry.get("tools") or () if isinstance(tool, str)})
