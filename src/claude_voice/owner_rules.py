"""The deny rules for what later runs as the owner (runs-as-owner v1.3).

permissions.json lists the paths in three kinds: under the home folder, in any clone, and in
the owner's own checkout. Each becomes Edit and Write rules, and a rule for each command
that writes a file there. A folder under home also gets a rule for each way of moving into
it. The commands that install code are listed as they are.
"""

from __future__ import annotations

#: How a path under the home folder is spelled in a shell command.
HOME_SPELLINGS = ("~/", "{home}/", "$HOME/")


def _edits(paths: list[str]) -> list[str]:
    """Edit and Write rules for each path pattern, as Claude Code reads a path rule."""
    return [f"{tool}({path})" for path in paths for tool in ("Edit", "Write")]


def _writes(targets: list[str], writers: list[str]) -> list[str]:
    """A shell rule for each writing command aimed at each target, `**` read as `*`."""
    return [f"Bash({w.replace('{path}', t.replace('**', '*'))})" for t in targets for w in writers]


def _folders(home: list[str]) -> list[str]:
    """Each protected folder under home, in every spelling of the home folder."""
    return [s + p[: -len("/**")] for s in HOME_SPELLINGS for p in home if p.endswith("/**")]


def _home(home: list[str], writers: list[str], movers: list[str]) -> list[str]:
    rules = _edits([f"~/{p}" for p in home] + [f"/{{home}}/{p}" for p in home])
    rules += _writes([s + p for s in HOME_SPELLINGS for p in home], writers)
    folders = _folders(home)
    rules += _writes(folders + [f + "/" for f in folders], writers)
    return rules + [f"Bash({m.replace('{folder}', f)})" for f in folders for m in movers]


def _clones(clones: list[str], own: list[str], writers: list[str]) -> list[str]:
    rules = _edits([f"**/{p}" for p in clones] + [f"//**/{p}" for p in clones])
    rules += _writes([f"*/{p}" for p in clones], writers)
    return rules + _edits([f"/{{folder}}/{p}" for p in own]) + _writes([f"{{folder}}/{p}" for p in own], writers)


def runs_as_owner(group: dict[str, list[str]]) -> list[str]:
    """Every deny rule of the group: home, clones, the owner's checkout, then the commands."""
    writers = group.get("writers", [])
    rules = _home(group.get("home", []), writers, group.get("movers", []))
    rules += _clones(group.get("any clone", []), group.get("own checkout", []), writers)
    return rules + [f"Bash({c})" for c in group.get("commands", [])]
