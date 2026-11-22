"""The fixed command the bridge runs in the approver's window (approver-v1, v1.7).

It asks the bridge for the approver's token over the report socket. The bridge hands it over
once, and only to this process, which tmux started as the window's own command. It then
becomes claude, by the absolute path the bridge gave it, with the token in its environment
and nowhere else: not on a command line, not in a file, and never printed.

Run as `python -I -m claude_voice.approver_start /abs/path/to/claude [claude's arguments]`.
"""

from __future__ import annotations

import json
import os
import socket
import sys
from pathlib import Path

from . import boxed, phone_hook

REFUSED = "The bridge did not hand this window the approver's token. Start the approver again from the bridge."


def ask(path: Path) -> str | None:
    """The approver's token from the bridge, or None when it does not hand one over."""
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as line:  # type: ignore[attr-defined,unused-ignore]
        line.settimeout(5)
        line.connect(str(Path(path).expanduser()))
        line.sendall(json.dumps({"ask": "token"}).encode() + b"\n")
        said = line.recv(256).decode().strip()
    return None if said in ("", "no") else said


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    # RULE: the starter runs claude only by an absolute path
    if not args or not os.path.isabs(args[0]):
        print("approver_start: the first argument must be the absolute path of claude.", file=sys.stderr)
        return 2
    try:
        token = ask(phone_hook.REPORT_SOCKET)
    except OSError:
        token = None
    if not token:
        print(REFUSED, file=sys.stderr)
        return 1
    # RULE: the starter gives claude the token in its environment only
    env = {**os.environ, boxed.TOKEN_VAR: token}
    try:
        os.execve(args[0], args, env)
    except OSError as exc:  # its message never holds the environment
        print(f"approver_start: claude could not be started ({exc.strerror}).", file=sys.stderr)
        return 1
    return 1  # only when execve was replaced, as in tests


if __name__ == "__main__":
    raise SystemExit(main())
