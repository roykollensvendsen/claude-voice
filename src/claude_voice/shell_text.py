"""The shell's own quoting, `$'...'` with escapes and `$"..."`, turned into plain text as bash reads it.

The push guard and the session hook both judge a command word by word, so both must see what
bash will see: `$'.cl\\x61ude'` is `.claude` to bash, and to them. One copy, used by both. It
uses the standard library only, since the session hook loads it.
"""

from __future__ import annotations

import re
import shlex
import sys

ANSI_C = re.compile(r"\$'((?:[^'\\]|\\.)*)'", re.DOTALL)
ESCAPE = re.compile(r"\\(x[0-9A-Fa-f]{1,2}|u[0-9A-Fa-f]{1,4}|U[0-9A-Fa-f]{1,8}|[0-7]{1,3}|c.|.)", re.DOTALL)
SIMPLE_ESCAPES = {
    "a": "\a",
    "b": "\b",
    "e": "\x1b",
    "E": "\x1b",
    "f": "\f",
    "n": "\n",
    "r": "\r",
    "t": "\t",
    "v": "\v",
    "\\": "\\",
    "'": "'",
    '"': '"',
    "?": "?",
}


def _escape(match: re.Match[str]) -> str:
    """The character one escape in `$'...'` stands for."""
    code = match.group(1)
    if code[0] in "xuU" and len(code) > 1:
        number = int(code[1:], 16)
        return chr(number) if number <= sys.maxunicode else ""
    if code[0] in "01234567":
        return chr(int(code, 8) & 0xFF)
    if code[0] == "c" and len(code) > 1:
        return chr(ord(code[1]) & 0x1F)
    # bash keeps the backslash of an escape it does not know, such as `\x` with no digits
    return SIMPLE_ESCAPES.get(code, "\\" + code)


def unquoted(command: str) -> str:
    """The command with the shell's own quoting turned into plain quoting, as bash reads it."""
    plain = ANSI_C.sub(lambda match: shlex.quote(ESCAPE.sub(_escape, match.group(1))), command)
    return plain.replace('$"', '"')
