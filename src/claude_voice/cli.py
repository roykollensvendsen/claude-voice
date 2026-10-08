"""Command line for claude-voice: serve the bridge, or check its configuration."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from .server import Config, ConfigError, load_config, serve_forever

#: Every verb this command has. The documentation test requires each one to
#: appear in a worked example, so a verb cannot be added without one.
COMMANDS: tuple[str, ...] = ("serve", "check")

SIGN_IN = (
    "Run `claude`, type /login and sign in with your Claude account, then check again. If `claude` "
    "is not found, install it first: `curl -fsSL https://claude.ai/install.sh | bash`."
)


def claude_code_login() -> tuple[bool, str]:
    """Whether the Claude Code the bridge runs is signed in, asked of Claude Code itself."""
    import claude_agent_sdk

    bundled = Path(claude_agent_sdk.__file__).parent / "_bundled" / "claude"
    cli = str(bundled) if bundled.exists() else shutil.which("claude")
    if not cli:
        return False, "Claude Code was not found"
    try:
        out = subprocess.run([cli, "auth", "status"], capture_output=True, text=True, timeout=30, check=False)
        status = json.loads(out.stdout)
    except (OSError, ValueError, subprocess.SubprocessError):
        return False, "Claude Code did not say whether it is signed in"
    if status.get("loggedIn"):
        return True, f"Claude Code is signed in ({status.get('authMethod', 'unknown method')})"
    return False, "Claude Code is not signed in"


def checks(
    env: Mapping[str, str], transport: str, login: Callable[[], tuple[bool, str]]
) -> tuple[Config | None, list[dict[str, Any]]]:
    """Every check, in the order a person would fix them, each with the step that fixes it."""
    found: list[dict[str, Any]] = []

    def add(name: str, ok: bool, detail: str, fix: str = "", required: bool = True) -> None:
        found.append({"name": name, "ok": ok, "required": required, "detail": detail, "fix": "" if ok else fix})

    cfg = None
    try:
        cfg = load_config(env, transport)
        add("configuration", True, "the configuration is usable")
    except ConfigError as exc:
        text = str(exc)
        name = (
            "api_key"
            if text.startswith("ANTHROPIC_API_KEY")
            else "project_root"
            if text.startswith("CLAUDE_VOICE_ROOT")
            else "secret"
            if text.startswith("CLAUDE_VOICE_TOKEN")
            else "configuration"
        )
        detail, _, fix = text.partition(". ")
        add(name, False, detail, fix)
    names = {c["name"] for c in found}
    if "project_root" not in names:
        root = Path(env.get("CLAUDE_VOICE_ROOT", "~/src")).expanduser()
        add("project_root", root.is_dir(), f"projects under {root}", f"Make it with `mkdir -p {root}`.")
    if transport == "http" and "secret" not in names:
        token = env.get("CLAUDE_VOICE_TOKEN", "")
        add("secret", len(token) >= 32, "a login secret is set", "Set CLAUDE_VOICE_TOKEN; see `claude-voice check`.")
    if env.get("ANTHROPIC_API_KEY") and env.get("CLAUDE_VOICE_ALLOW_API_KEY") == "1":
        add("signed_in", True, "Claude Code uses the API key")
    else:
        ok, detail = login()
        add("signed_in", ok, detail, SIGN_IN)
    if transport == "http":
        public = bool(env.get("CLAUDE_VOICE_PUBLIC_HOSTS") or env.get("CLAUDE_VOICE_PUBLIC_URL"))
        add(
            "public_address",
            public,
            "a public address is set" if public else "no public address, so only this computer can connect",
            "To reach it from a phone, set CLAUDE_VOICE_PUBLIC_HOSTS to the tunnel's hostname (see the README).",
            required=False,
        )
    return cfg, found


def main(
    argv: list[str] | None = None,
    env: Mapping[str, str] = os.environ,
    serve: Callable[[Config], None] = serve_forever,
    login: Callable[[], tuple[bool, str]] = claude_code_login,
) -> int:
    """Parse arguments and dispatch. Returns a process exit code."""
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] not in (*COMMANDS, "-h", "--help"):
        args.insert(0, "serve")  # older service files call it without a verb

    parser = argparse.ArgumentParser(prog="claude-voice", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name, text in (
        ("serve", "run the MCP bridge"),
        ("check", "check that the bridge could start, say what to fix, then exit"),
    ):
        p = sub.add_parser(name, help=text)
        p.add_argument("--transport", choices=["stdio", "http"], default="stdio")
        if name == "check":
            p.add_argument("--json", action="store_true", help="answer in JSON, for an assistant doing the setup")
    parsed = parser.parse_args(args)

    if parsed.command == "check":
        cfg, found = checks(env, parsed.transport, login)
        ready = all(c["ok"] for c in found if c["required"])
        if parsed.json:
            sys.stdout.write(json.dumps({"ready": ready, "checks": found}, indent=2) + "\n")
        elif not ready:
            first = next(c for c in found if c["required"] and not c["ok"])
            sys.stderr.write(f"claude-voice: {first['detail']}. {first['fix']}".rstrip() + "\n")
        else:
            assert cfg is not None
            sys.stdout.write(f"claude-voice: ready to serve over {cfg.transport}; projects under {cfg.root}\n")
            for c in found:
                if not c["ok"]:
                    sys.stdout.write(f"claude-voice: note: {c['detail']}. {c['fix']}\n")
        return 0 if ready else 1

    try:
        cfg = load_config(env, parsed.transport)
    except ConfigError as exc:
        sys.stderr.write(f"claude-voice: {exc}\n")
        return 1
    serve(cfg)
    return 0


def entry() -> None:
    sys.exit(main())


if __name__ == "__main__":
    entry()
