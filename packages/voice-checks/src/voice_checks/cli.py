"""`voice-checks`: the command each plugin runs, in its own folder."""

import argparse
import pathlib
import sys

from voice_checks import bump, docs, temp_files


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


def _docs(args: argparse.Namespace) -> int:
    root = pathlib.Path(args.root)
    if args.write:
        try:
            docs.write(root)
        except docs.DocsError as refused:
            print(f"voice-checks: {refused}")
            return 1
    found = docs.problems(root)
    for line in found:
        print(f"voice-checks: {line}")
    if found:
        return 1
    print("voice-checks: the setup guide can be followed, and the README's step list is made from it.")
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
    guide = commands.add_parser("docs", help="check the setup guide and the README's step list made from it")
    guide.add_argument("--root", default=".", help="the plugin's checkout (default: here)")
    guide.add_argument("--write", action="store_true", help="write the README's step list from the guide first")
    guide.set_defaults(run=_docs)
    args = parser.parse_args(argv)
    result: int = args.run(args)
    return result


if __name__ == "__main__":
    sys.exit(main())
