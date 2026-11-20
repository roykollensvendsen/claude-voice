"""Move a plugin's one voice-checks pin to a newer commit of claude-voice."""

import pathlib
import re
import subprocess

REPO = "https://github.com/roykollensvendsen/claude-voice"
PIN = re.compile(r"(voice-checks @ git\+" + re.escape(REPO) + r"@)([0-9a-f]{40})(#subdirectory=packages/voice-checks)")
SHA = re.compile(r"[0-9a-f]{40}")
TAG = re.compile(r"refs/tags/voice-checks-v(\d+)\.(\d+)\.(\d+)(\^\{\})?$")


class BumpError(Exception):
    """A pin that cannot be moved, said as what to do instead."""


def moved(pyproject: str, sha: str) -> str:
    """The pyproject text with its voice-checks pin at `sha`, and nothing else changed."""
    if not SHA.fullmatch(sha):
        msg = f"{sha!r} is not a commit: give all 40 hex characters of one."
        raise BumpError(msg)
    if not PIN.search(pyproject):
        msg = f"There is no voice-checks pin to move. Add one to the dev group: voice-checks @ git+{REPO}@<sha>..."
        raise BumpError(msg)
    return PIN.sub(lambda m: m[1] + sha + m[3], pyproject)


def newest_release(listed: str) -> str:
    """The commit of the newest voice-checks-v* tag, from `git ls-remote --tags` output.

    Versions compare as numbers, so 0.10 is newer than 0.9. An annotated tag's own line
    names the tag object, and its `^{}` line the commit, which is the one to pin.
    """
    tags: dict[tuple[int, int, int], dict[bool, str]] = {}
    for line in listed.splitlines():
        sha, _, ref = line.partition("\t")
        found = TAG.search(ref)
        if found:
            version = (int(found[1]), int(found[2]), int(found[3]))
            tags.setdefault(version, {})[found[4] is not None] = sha
    if not tags:
        msg = "claude-voice has no voice-checks-v* release tag yet. Pin a commit: voice-checks bump --sha <sha>."
        raise BumpError(msg)
    newest = tags[max(tags)]
    return newest.get(True) or newest[False]


def latest() -> str:
    """The commit of claude-voice's newest voice-checks release tag, asked of GitHub."""
    listed = subprocess.run(  # noqa: S603 - a fixed command, no shell
        ["git", "ls-remote", "--tags", REPO, "voice-checks-v*"],
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    return newest_release(listed.stdout)


def bump(pyproject: pathlib.Path, sha: str) -> None:
    """Write the moved pin; `uv lock` afterwards brings the lock file along."""
    pyproject.write_text(moved(pyproject.read_text(encoding="utf-8"), sha), encoding="utf-8")
