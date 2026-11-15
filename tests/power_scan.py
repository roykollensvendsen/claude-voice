"""The static scans of the power code: what it must never contain.

Used on the helper's source here, and on the agent's relay beside it. Each scan is given
a bad sample in its own test, so a scan that finds nothing is known to be able to find.
"""

from __future__ import annotations

import ast
import re

#: Words that mean a privilege tool, a password or a way round an inhibitor.
FORBIDDEN_WORDS = re.compile(r"sudo|pkexec|password|ignore-inhibit", re.IGNORECASE)
#: Commands the login manager's own D-Bus calls make unnecessary.
FORBIDDEN_COMMANDS = {"systemctl", "shutdown", "loginctl", "busctl", "dbus-send", "poweroff", "reboot"}
RUNNERS = {"run", "Popen", "call", "check_call", "check_output", "system", "popen", "spawn", "exec"}


def words(text: str) -> list[str]:
    """Every forbidden word in `text`."""
    return sorted({m.group(0).lower() for m in FORBIDDEN_WORDS.finditer(text)})


def commands(source: str) -> list[str]:
    """Every forbidden command named in an argument of a call that runs something."""
    found = set()
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and getattr(func.value, "id", "") not in ("subprocess", "os"):
            continue  # a method of something else, such as a bus's own `call`
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        if name.startswith("exec") or name in RUNNERS:
            for inner in ast.walk(node):
                if isinstance(inner, ast.Constant) and isinstance(inner.value, str):
                    found.update(w for w in re.split(r"[\s/]+", inner.value) if w in FORBIDDEN_COMMANDS)
    return sorted(found)
