"""Command line for claude-voice: serve the bridge, or check its configuration."""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Callable, Mapping

from .server import Config, ConfigError, load_config, serve_forever

#: Every verb this command has. The documentation test requires each one to
#: appear in a worked example, so a verb cannot be added without one.
COMMANDS: tuple[str, ...] = ("serve", "check")


def main(
    argv: list[str] | None = None,
    env: Mapping[str, str] = os.environ,
    serve: Callable[[Config], None] = serve_forever,
) -> int:
    """Parse arguments and dispatch. Returns a process exit code."""
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] not in COMMANDS:
        args.insert(0, "serve")  # older service files call it without a verb

    parser = argparse.ArgumentParser(prog="claude-voice", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name, text in (
        ("serve", "run the MCP bridge"),
        ("check", "check the configuration from the environment, then exit"),
    ):
        p = sub.add_parser(name, help=text)
        p.add_argument("--transport", choices=["stdio", "http"], default="stdio")
    parsed = parser.parse_args(args)

    try:
        cfg = load_config(env, parsed.transport)
    except ConfigError as exc:
        sys.stderr.write(f"claude-voice: {exc}\n")
        return 1
    if parsed.command == "check":
        sys.stdout.write(f"claude-voice: ready to serve over {cfg.transport}; projects under {cfg.root}\n")
        return 0
    serve(cfg)
    return 0


def entry() -> None:
    sys.exit(main())


if __name__ == "__main__":
    entry()
