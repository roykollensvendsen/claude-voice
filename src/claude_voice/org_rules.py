"""The rules a session is told when it starts (org-rules v1.2, rule-packs v1.1).

A role's settings run `python -m claude_voice.org_rules emit <org> --event <E> --shared-sha
<12> --sha <12>` as a SessionStart and a SubagentStart hook. It reads the shared pack, then
the organisation's own, from the owner's settings folder only, never from the session's
folder. Each must match the sha the role file recorded, and the two together must fit the
cap. It prints them as the hook's context.

Whatever goes wrong, it prints one line telling the session to stop and to tell its
manager, and exits 0. A hook that fails is silent to the session, so failing loudly in its
context is the only way the session learns its rules are missing.

The packs are never committed to any repository: they hold the owner's own decisions, and
for some organisations names that must stay on this computer.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
from pathlib import Path
from typing import IO, Any, NoReturn

__all__ = ["CAP", "PACKS", "context", "main", "sha"]

#: Where the packs are installed: the owner's settings folder, outside every repository.
PACKS = Path("~/.config/claude-voice/orgs").expanduser()
SHARED = "_shared"
#: Above about 10 000 characters a session is shown only a short preview of a hook's
#: context, so the tail of the rules would be lost (measured on decoys, org-rules v1.2 §1).
CAP = 9000
EVENTS = ("SessionStart", "SubagentStart")
#: An organisation is named in lower-case letters, digits and dashes, so it can never
#: reach outside the packs folder or name the shared pack.
NAME = re.compile(r"[a-z0-9][a-z0-9-]*")
STOP = "ORG RULES {reason}: stop, do no work, and tell your manager in one message"
#: How a hook runs this module: with the bridge's own Python, never a session's, and
#: isolated, so nothing in the session's folder or environment is imported in its place.
# RULE: the hook runs isolated from the sessions folder
COMMAND = (sys.executable, "-I", "-m", "claude_voice.org_rules")


class Refused(Exception):
    """Why the rules cannot be given; the text goes into the stop line."""


def sha(data: bytes) -> str:
    """The first twelve hex digits of the sha256, as role files record it."""
    return hashlib.sha256(data).hexdigest()[:12]


def _read(root: Path, org: str) -> bytes:
    """One pack's bytes, refused when the file or a folder on the way is a link."""
    path = root.resolve() / org / "rules.md"  # the owner may link his own settings folder
    # RULE: a linked rules file is refused
    if path.parent.is_symlink():
        raise Refused(f"{org}'s pack folder is a link")
    # RULE: a pipe in place of a pack is refused without waiting
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        fd = os.open(path, flags)
    except FileNotFoundError:
        raise Refused(f"{org}'s pack is missing at {path}") from None
    except OSError as exc:
        raise Refused(f"{org}'s pack could not be read ({exc.strerror})") from None
    try:
        # RULE: only a plain file is read as a pack
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise Refused(f"{org}'s pack at {path} is not a plain file")
        data = b""
        while chunk := os.read(fd, 1 << 16):
            data += chunk
        return data
    except OSError as exc:
        raise Refused(f"{org}'s pack could not be read ({exc.strerror})") from None
    finally:
        os.close(fd)


def _pack(root: Path, org: str, want: str) -> str:
    data = _read(root, org)
    # RULE: a pack that does not match its sha gives the stop line
    if sha(data) != want:
        raise Refused(f"{org}'s pack has sha {sha(data)}, not the {want} the role file recorded")
    try:
        return data.decode()
    except UnicodeDecodeError:
        raise Refused(f"{org}'s pack is not UTF-8 text") from None


def context(org: str, shared_sha: str, own_sha: str, root: Path = PACKS) -> str:
    """The shared rules, then the organisation's own, or the stop line saying why not."""
    try:
        # RULE: only a plain organisation name is read
        if not NAME.fullmatch(org):
            raise Refused(f"{org!r} is not an organisation's name")
        shared = _pack(root, SHARED, shared_sha)
        own = _pack(root, org, own_sha)
        text = f"shared rules (sha {shared_sha}):\n{shared}\n\n{org} rules (sha {own_sha}):\n{own}"
        size = len(json.dumps(text).encode())
        # RULE: the cap counts the bytes the session is sent
        if size > CAP:
            raise Refused(f"the shared and {org} packs are {size} bytes together, over the {CAP}-byte cap")
        return text
    except Refused as why:
        return STOP.format(reason=why)


def _said(event: str, text: str) -> dict[str, Any]:
    return {"hookSpecificOutput": {"hookEventName": event, "additionalContext": text}}


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise Refused(f"the hook's command is wrong ({message})")


def _parser() -> _Parser:
    # a shortened option is never taken for a whole one: the role file spells each out
    parser = _Parser(prog="python -I -m claude_voice.org_rules", add_help=False, allow_abbrev=False)
    sub = parser.add_subparsers(dest="action", required=True, parser_class=_Parser)
    emit = sub.add_parser("emit", add_help=False, allow_abbrev=False)
    emit.add_argument("org")
    emit.add_argument("--event", required=True)
    emit.add_argument("--shared-sha", required=True)
    emit.add_argument("--sha", required=True)
    return parser


def main(
    argv: list[str] | None = None,
    root: Path = PACKS,
    stdin: IO[str] = sys.stdin,
    stdout: IO[str] = sys.stdout,
) -> int:
    """Run as a hook: print the rules or the stop line, and exit 0 whatever happened."""
    del stdin  # every source of a start gets the same rules, compact included (rule-packs f1)
    event = EVENTS[0]
    try:
        args = _parser().parse_args(sys.argv[1:] if argv is None else argv)
        if args.event not in EVENTS:
            raise Refused(f"{args.event!r} is not one of {', '.join(EVENTS)}")
        event = args.event
        text = context(args.org, args.shared_sha, args.sha, root)
    except Refused as why:
        text = STOP.format(reason=why)
    # RULE: an unexpected error still says stop and exits 0
    except Exception as exc:  # a crash is silent to the session, so it would start without rules
        text = STOP.format(reason=f"the hook failed ({type(exc).__name__}: {exc})")
    # RULE: a wrong hook command still says stop and exits 0
    stdout.write(json.dumps(_said(event, text)) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
