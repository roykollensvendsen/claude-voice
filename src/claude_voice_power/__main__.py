"""`python -I -m claude_voice_power` serves; `claude-voice-power cancel` stops a minute."""

from __future__ import annotations

import logging
import os
import sys
from collections.abc import Callable

from . import core, selfcheck, server, signing
from .logind import DBusLogind, JeepneyBus

log = logging.getLogger("claude_voice_power")


def serve(keep_going: Callable[[], bool] = lambda: True) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if not selfcheck.check(log.error):
        return 1
    machine, key, night = server.read_settings()
    helper = core.Helper(DBusLogind(JeepneyBus()), signing.Verifier(key), machine, server.WallNotifier(), night=night)
    listener = server.listening_socket(os.environ)
    log.info("serving %s", machine)
    while keep_going():
        server.serve_one(listener, helper)
    return 0


def cancel() -> int:
    try:
        sys.stdout.write(server.ask({"op": "cancel"}).get("say", "") + "\n")
    except (OSError, ValueError) as exc:
        sys.stderr.write(f"Could not reach the power helper ({exc}); is claude-voice-power.socket running?\n")
        return 1
    return 0


def entry() -> None:
    sys.exit(cancel() if sys.argv[1:2] == ["cancel"] else serve())


if __name__ == "__main__":
    entry()
