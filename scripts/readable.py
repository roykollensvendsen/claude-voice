#!/usr/bin/env python3
"""Say when a commit body is harder to read than it needs to be.

Every page in these repositories lives under a sentence-length rule, and
commit messages escaped it: measured across one repository's history, a
quarter of the sentences in its bodies broke the limit no page may break,
and the median body ran to 136 words. A reader then has to ask what the
change actually did, which is the sentence the message should have opened
with.

    python scripts/readable.py --file .git/COMMIT_EDITMSG
    python scripts/readable.py origin/main..HEAD

This only ever warns. It exits 0 whatever it finds, because how long a
sentence needs to be is a judgement, and a gate that refuses a message
nobody can shorten teaches people to pass --no-verify.
"""

from __future__ import annotations

import pathlib
import re
import subprocess
import sys

MAX_MEAN = 20
MAX_SENTENCE = 30
TARGET_WORDS = 80
MIN_WORDS = 2  # below this a fragment is a label, not a sentence to measure
TRAILER = re.compile(r"^[A-Z][\w-]+: .*$")


def body_of(message: str) -> str:
    """The message without its subject, its trailers and its code blocks."""
    lines = message.splitlines()[1:]
    kept = [x for x in lines if not TRAILER.match(x.strip()) and not x.startswith("#")]
    text = "\n".join(kept)
    return re.sub(r"```.*?```", "", text, flags=re.DOTALL)


def sentences(body: str) -> list[str]:
    """The body's sentences, dropping fragments too short to judge."""
    return [
        x.strip()
        for para in re.split(r"\n\s*\n", body)
        for x in re.split(r"(?<=[.!?])\s+", re.sub(r"\s+", " ", para))
        if len(x.split()) > MIN_WORDS
    ]


def findings(message: str) -> list[str]:
    """What makes this body harder to read than it needs to be."""
    body = body_of(message)
    found = sentences(body)
    if not found:
        return []
    said = []
    words = len(body.split())
    lengths = [len(s.split()) for s in found]
    mean = sum(lengths) / len(lengths)
    if mean > MAX_MEAN:
        said.append(f"{mean:.0f} words per sentence on average; under {MAX_MEAN} reads faster")
    for sentence, count in zip(found, lengths, strict=True):
        if count > MAX_SENTENCE:
            said.append(f"a sentence of {count} words: {sentence[:60]}...")
    if words > TARGET_WORDS * 2:
        said.append(f"{words} words; about {TARGET_WORDS} is the aim, and longer should earn it")
    return said


def messages(where: str) -> list[tuple[str, str]]:
    """Every message in a range, or the one in a file."""
    if where.startswith("--file="):
        path = pathlib.Path(where.removeprefix("--file="))
        return [(path.name, path.read_text())]
    result = subprocess.run(  # noqa: S603
        ["git", "log", "--no-merges", "--format=%H%x00%B%x01", where],  # noqa: S607
        capture_output=True,
        text=True,
        check=True,
    )
    out = []
    for chunk in result.stdout.split("\x01"):
        if "\x00" in chunk:
            sha, message = chunk.strip().split("\x00", 1)
            out.append((sha[:7], message))
    return out


def main() -> int:
    """Warn, and never refuse."""
    args = sys.argv[1:]
    where = "--file=" + args[1] if args[:1] == ["--file"] else (args[0] if args else "HEAD~1..HEAD")
    for name, message in messages(where):
        for said in findings(message):
            print(f"{name}: {said}")  # noqa: T201
    return 0


if __name__ == "__main__":
    sys.exit(main())
