#!/usr/bin/env python3
"""Refuse a commit that would publish where the owner lives, or a word they keep private.

A plugin repository is often public. Nothing in it may name the owner's street, their home
network or their exact position. The checks, on every file given (by default the files staged
for commit), on a commit message, or on the whole history:

- no home-network address: a private IPv4 address (examples use 192.0.2.x);
- no street address, such as a Norwegian street name followed by a number;
- no coordinates more exact than two decimals;
- no link to a Claude session, which points into one person's account;
- no word from the owner's own private list, such as a device's own name.

The private list is ``~/.config/gizmo-mcp/private.toml``, holding ``words = ["...", ...]``.
It lives outside the repository, so the list itself is never published, and it is optional:
with no file there, that check is skipped. A list that is there and cannot be read refuses the
commit instead, since a broken list would otherwise check nothing and look like a pass.

What is found is reported by kind and line, never quoted, since the report may land in a
public log.

    python3 scripts/private_data_scan.py            # the staged files (pre-commit)
    python3 scripts/private_data_scan.py --tracked  # every tracked file
    python3 scripts/private_data_scan.py --message .git/COMMIT_EDITMSG  # (commit-msg)
    python3 scripts/private_data_scan.py --tracked --history            # CI
"""

from __future__ import annotations

import argparse
import pathlib
import re
import subprocess
import sys
import tomllib

ROOT = pathlib.Path(__file__).resolve().parent.parent
WORDS = pathlib.Path("~/.config/gizmo-mcp/private.toml")
#: Shorter words would match ordinary text.
MIN_WORD = 4

PRIVATE_IPV4 = re.compile(r"(?<![\d.])(?:10\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01])|192\.168)\.\d{1,3}\.\d{1,3}(?![\d.])")
STREET = re.compile(
    r"\b[A-ZÆØÅ][a-zæøå]+(?:gata|gaten|gate|veien|vegen|vei|veg|torget|plassen|allé|alleen)\s+\d{1,4}[A-Za-z]?\b"
)
#: Any latitude and longitude written to three decimals or more, which is a house, not a town.
COORDINATES = re.compile(r"(?<![\d.])-?\d{1,2}\.\d{3,}\s*,\s*-?\d{1,3}\.\d{3,}(?![\d.])")
#: A link into a Claude session, however the trailer is written.
SESSION_LINK = re.compile(r"claude-session\s*:|claude\.ai/code/session_", re.IGNORECASE)


class UnreadableListError(Exception):
    """The private list is there but cannot be used; the message names the fix."""


def private_words(words: pathlib.Path) -> set[str]:
    """The owner's private words; none when there is no list at all."""
    # A link whose target is gone is not "no list": the owner meant one to be there.
    if not words.exists() and not words.is_symlink():
        return set()
    # RULE: a private list that cannot be read refuses the commit
    if not words.is_file():
        msg = f"{words} is there but is not a readable file; make it a file of words, or remove it"
        raise UnreadableListError(msg)
    try:
        listed = tomllib.loads(words.read_text(encoding="utf-8")).get("words", [])
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as broken:
        msg = f"{words} cannot be read as TOML ({broken}); fix it so it holds words = [...]"
        raise UnreadableListError(msg) from broken
    return {w for w in listed if isinstance(w, str) and len(w) >= MIN_WORD}


def offences(text: str, private: set[str]) -> list[str]:
    """What in ``text`` would say where the owner lives, or name what they keep private, by kind only."""
    found = [f"line {_line(text, m.start())}: a home-network address" for m in PRIVATE_IPV4.finditer(text)]
    found += [f"line {_line(text, m.start())}: a street address" for m in STREET.finditer(text)]
    found += [f"line {_line(text, m.start())}: exact coordinates" for m in COORDINATES.finditer(text)]
    found += [f"line {_line(text, m.start())}: a link to a Claude session" for m in SESSION_LINK.finditer(text)]
    lowered = text.lower()
    for value in private:
        at = lowered.find(value.lower())
        if at >= 0:
            found.append(f"line {_line(text, at)}: a word from the private list")
    return found


def _line(text: str, at: int) -> int:
    return text.count("\n", 0, at) + 1


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout


def _texts(*, tracked: bool, history: bool, message: str | None = None) -> list[tuple[str, str]]:
    """Each place to scan, as (what it is, its text)."""
    if message is not None:
        # The lines git drops from a message are not part of it.
        lines = pathlib.Path(message).read_text(encoding="utf-8").splitlines()
        return [("the commit message", "\n".join(x for x in lines if not x.startswith("#")))]
    out = []
    if history:
        out.append(("the history", _git("log", "-p", "--all", "--no-color", "--format=commit %H%n%B")))
    names = _git("ls-files") if tracked else ("" if history else _git("diff", "--cached", "--name-only"))
    for name in names.splitlines():
        try:
            out.append((name, (ROOT / name).read_text(encoding="utf-8")))
        except (FileNotFoundError, IsADirectoryError, UnicodeDecodeError):
            continue
    return out


def main(argv: list[str] | None = None) -> int:
    """Scan the staged files, every tracked file, a message or the history; 1 when anything is found."""
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--tracked", action="store_true", help="scan every tracked file")
    parser.add_argument("--history", action="store_true", help="scan every commit on every branch")
    parser.add_argument("--message", help="scan this commit message file instead (the commit-msg hook)")
    parser.add_argument("--words", default=str(WORDS), help=f"the private list (default {WORDS})")
    args = parser.parse_args(argv)
    try:
        private = private_words(pathlib.Path(args.words).expanduser())
    except UnreadableListError as unreadable:
        sys.stderr.write(f"{unreadable}\n")
        return 1
    bad = 0
    for where, text in _texts(tracked=args.tracked, history=args.history, message=args.message):
        for offence in offences(text, private):
            sys.stderr.write(f"{where}, {offence}\n")
            bad += 1
    if bad:
        sys.stderr.write("Use made-up names, 192.0.2.x, and two decimals at most in examples.\n")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
