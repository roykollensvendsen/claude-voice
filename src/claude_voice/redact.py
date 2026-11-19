"""Hiding secrets in text the bridge sends on, such as a waiting question read off a screen.

Text from a session's screen or a tool's input can hold a token, a password or a signed
link. Before such text is sent to the voice, the phone or a manager, each secret it can
recognise becomes ``[hidden]``, and only then is the text cut short, so a cut can never
leave half a secret showing (docs/event-api.md, "A question left waiting").

It hides by shape, so it hides too much rather than too little: a word that merely looks
like a setting holding a key loses its value too.
"""

from __future__ import annotations

import re

HIDDEN = "[hidden]"

PRIVATE_KEY = re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?(?:-----END [A-Z0-9 ]*PRIVATE KEY-----|\Z)", re.S)
TOKENS = re.compile(
    r"\b(?:gh[pousr]_[A-Za-z0-9]{16,}|github_pat_[A-Za-z0-9_]{16,}|sk-[A-Za-z0-9_-]{16,}"
    r"|xox[abprs]-[A-Za-z0-9-]{8,}|AKIA[A-Z0-9]{16}"
    r"|glpat-[A-Za-z0-9_-]{16,}|hf_[A-Za-z0-9]{16,}|npm_[A-Za-z0-9]{16,}|AIza[0-9A-Za-z_-]{30,}"
    r"|[sr]k_(?:live|test)_[A-Za-z0-9]{8,}"
    r"|eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{4,})"
)
USERINFO = re.compile(r"(?<=://)[^/\s@:]+:[^/\s@]*@")
QUERY = re.compile(r"([?&][A-Za-z0-9_.~%-]+=)(?!\[hidden\])[^&\s#'\"]+")
SCHEME = re.compile(r"\b(Bearer|Basic)\s+(?!\[hidden\])[^\s'\"]+")
#: A quote around a name or value: plain, double, curly, or the one a said question turns them into.
Q = "['\"ʼ“”‘’`]"
SECRET_WORD = r"[A-Za-z0-9_-]*(?:key|token|secret|pass|auth)[A-Za-z0-9_-]*"
NAMED = re.compile(
    rf"(?<![A-Za-z0-9_-])({SECRET_WORD}{Q}?\s*[=:]\s*)({Q}?)(?!\[hidden\])[^\s'\"ʼ&,}}]+",
    re.I,
)
#: A flag whose name holds a secret word, then its value after a space; and the forms that put
#: a password in a flag of their own.
FLAG = re.compile(
    rf"(\s--?{SECRET_WORD}\s+|\bsecret\s+set\s+\S+\s+(?:--body|-b)\s+|\s-u\s+[^\s:]+:"
    rf"|\b(?:mysql|mariadb|mysqldump|mysqladmin)\b[^\n]*?\s-p)({Q}?)(?!\[hidden\])(?!-)[^\s'\"ʼ]+",
    re.I,
)


def mask(text: str) -> str:
    """`text` with each secret it can recognise replaced by [hidden]."""
    text = PRIVATE_KEY.sub(HIDDEN, text)
    # RULE: every token shape is hidden
    text = TOKENS.sub(HIDDEN, text)
    text = USERINFO.sub(HIDDEN + "@", text)
    text = QUERY.sub(lambda m: m.group(1) + HIDDEN, text)
    text = SCHEME.sub(lambda m: f"{m.group(1)} {HIDDEN}", text)
    # RULE: the value after a secret flag is hidden
    text = FLAG.sub(lambda m: m.group(1) + m.group(2) + HIDDEN, text)
    # RULE: the value after a secret name is hidden
    return NAMED.sub(lambda m: m.group(1) + m.group(2) + HIDDEN, text)


def hide(text: str, limit: int) -> str:
    """`text` masked, then cut to at most `limit` characters."""
    # RULE: secrets are hidden before the text is cut
    shown = mask(text)
    return shown if len(shown) <= limit else shown[: limit - 1] + "…"
