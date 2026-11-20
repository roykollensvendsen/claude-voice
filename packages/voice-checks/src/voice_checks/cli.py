"""`voice-checks`: the command each plugin runs, in its own folder."""

import argparse
import pathlib
import sys

from voice_checks import bump, temp_files


def _temp_files(args: argparse.Namespace) -> int:
    found = temp_files.scan(pathlib.Path(args.root))
    for line in found:
        print(line)
    if found:
        return 1
    print("voice-checks: no temporary file or folder can be left behind.")
    return 0


def _bump(args: argparse.Namespace) -> int:
    path = pathlib.Path(args.pyproject)
    try:
        sha = args.sha or bump.latest()
        bump.bump(path, sha)
    except bump.BumpError as refused:
        print(f"voice-checks: {refused}", file=sys.stderr)
        return 1
    print(f"voice-checks: {path} now pins {sha}. Run `uv lock` to bring the lock file along.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="voice-checks", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    temps = commands.add_parser("temp-files", help="refuse a temporary file or folder that may be left behind")
    temps.add_argument("--root", default=".", help="the plugin's checkout (default: here)")
    temps.set_defaults(run=_temp_files)
    bumps = commands.add_parser("bump", help="move the voice-checks pin to the newest release")
    bumps.add_argument("--pyproject", default="pyproject.toml", help="the file holding the pin")
    bumps.add_argument("--sha", help="pin this commit instead of the newest release")
    bumps.set_defaults(run=_bump)
    args = parser.parse_args(argv)
    result: int = args.run(args)
    return result


if __name__ == "__main__":
    sys.exit(main())
