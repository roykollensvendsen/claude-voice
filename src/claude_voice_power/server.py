"""The helper's socket: one JSON line in, one JSON line out, and a look at the clock between.

systemd opens the socket and hands it over as file descriptor 3; the helper never makes
its own. It serves one connection at a time, so its decisions never race each other.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from . import core, signing
from .units import SOCKET_PATH

assert sys.platform != "win32"  # the helper runs on Linux only: a Unix socket and systemd

ETC = Path("/etc/claude-voice-power")
LISTEN_FD = 3
MAX_LINE = 4096
TICK_SECONDS = 0.5


class WallNotifier:
    """Tells every terminal on the machine, with `wall`. It never fails the helper."""

    def tell(self, text: str) -> None:
        try:
            subprocess.run(["wall", text], check=False, timeout=5, capture_output=True)
        except (OSError, subprocess.SubprocessError):
            pass


def listening_socket(env: Mapping[str, str], first_fd: int = LISTEN_FD) -> socket.socket:
    """The socket systemd passed in, or an error saying it was not started by the socket unit."""
    if env.get("LISTEN_PID") != str(os.getpid()) or env.get("LISTEN_FDS") != "1":
        raise SystemExit("claude-voice-power must be started by claude-voice-power.socket")
    return socket.socket(fileno=first_fd)


def read_settings(etc: Path = ETC) -> tuple[str, bytes, tuple[int, int]]:
    """This machine's name, the hub's public key, and the night hours."""
    machine = (etc / "machine").read_text().strip()
    key = signing.parse_public_key((etc / "hub.pub").read_text())
    night = core.NIGHT
    try:
        found = tomllib.loads((etc / "config.toml").read_text())
        night = (int(found["night_start"]), int(found["night_end"]))
    except (OSError, KeyError, ValueError):
        pass
    return machine, key, night


def serve_one(listener: socket.socket, helper: core.Helper) -> None:
    """Answer the next connection if there is one within a tick, then let the minute run."""
    listener.settimeout(TICK_SECONDS)
    try:
        connection, _ = listener.accept()
    except TimeoutError:
        helper.tick()
        return
    with connection:
        connection.settimeout(2.0)
        answer = helper.handle(_read(connection))
        connection.sendall(json.dumps(answer).encode() + b"\n")
    helper.tick()


def _read(connection: socket.socket) -> dict[str, Any]:
    try:
        line = connection.makefile("rb").readline(MAX_LINE)
        found = json.loads(line)
    except (OSError, ValueError):
        return {}
    return found if isinstance(found, dict) else {}


def ask(message: dict[str, Any], path: str = SOCKET_PATH) -> dict[str, Any]:
    """One message to the helper and its answer: how the agent, and `cancel`, reach it."""
    with socket.socket(socket.AF_UNIX) as client:
        client.settimeout(5.0)
        client.connect(path)
        client.sendall(json.dumps(message).encode() + b"\n")
        found = json.loads(client.makefile("rb").readline(MAX_LINE))
    return found if isinstance(found, dict) else {}
