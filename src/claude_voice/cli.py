"""Command line for claude-voice: serve the bridge, or check its configuration."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from . import procs
from .server import Config, ConfigError, load_config, serve_forever

#: Every verb this command has. The documentation test requires each one to
#: appear in a worked example, so a verb cannot be added without one.
COMMANDS: tuple[str, ...] = ("serve", "check", "plugins", "agent", "machines", "permissions")

SIGN_IN = (
    "Run `claude`, type /login and sign in with your Claude account, then check again. If `claude` "
    "is not found, install it first: `curl -fsSL https://claude.ai/install.sh | bash`."
)


def claude_code_login() -> tuple[bool, str]:
    """Whether the Claude Code the bridge runs is signed in, asked of Claude Code itself."""
    import claude_agent_sdk

    bundled = Path(claude_agent_sdk.__file__).parent / "_bundled" / "claude"
    cli = str(bundled) if bundled.exists() else procs.find("claude")
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
    plugin_cmd = sub.add_parser("plugins", help="list plugins, or approve one by typing its name")
    plugin_cmd.add_argument("action", nargs="?", choices=["list", "approve"], default="list")
    plugin_cmd.add_argument("name", nargs="?")
    agent_cmd = sub.add_parser("agent", help="on another machine: answer the paired hub, or pair with one")
    agent_cmd.add_argument(
        "action",
        nargs="?",
        choices=["serve", "pair", "install"],
        default="serve",
        help="serve (the default), pair, or install as a service",
    )
    agent_cmd.add_argument("--sessions", action="store_true", help="with install: offer this machine's sessions")
    machine_cmd = sub.add_parser("machines", help="list other machines, or pair with one by typing its code")
    machine_cmd.add_argument("action", nargs="?", choices=["list", "approve"], default="list")
    machine_cmd.add_argument("name", nargs="?")
    permission_cmd = sub.add_parser("permissions", help="give Claude Code the voice repositories' permission rules")
    permission_cmd.add_argument("folders", nargs="*", type=Path, help="default: the clones in the settings file")
    permission_cmd.add_argument("--dry-run", action="store_true", help="say what would be added, and write nothing")
    parsed = parser.parse_args(args)

    if parsed.command == "permissions":
        return permissions_command(parsed.folders, parsed.dry_run, env)

    if parsed.command == "machines":
        return machines_command(parsed.action, parsed.name, env)

    if parsed.command == "agent":
        return agent_command(parsed.action, env, parsed.sessions)

    if parsed.command == "plugins":
        return plugins_command(parsed.action, parsed.name, env)

    if parsed.command == "check":
        return check_command(env, parsed.transport, parsed.json, login)

    try:
        cfg = load_config(env, parsed.transport)
    except ConfigError as exc:
        sys.stderr.write(f"claude-voice: {exc}\n")
        return 1
    serve(cfg)
    return 0


def check_command(env: Mapping[str, str], transport: str, as_json: bool, login: Callable[[], tuple[bool, str]]) -> int:
    """Say whether the bridge could start, and what to fix first."""
    cfg, found = checks(env, transport, login)
    ready = all(c["ok"] for c in found if c["required"])
    if as_json:
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


def plugins_command(action: str, name: str | None, env: Mapping[str, str]) -> int:
    """List the plugins in the settings file, or approve one: the owner types its name."""
    from . import config as settings
    from . import plugins

    try:
        specs = settings.load(settings.FILE, env=env).plugins
    except settings.ConfigError as exc:
        sys.stderr.write(f"claude-voice: {exc}\n")
        return 1
    if action == "list":
        if not specs:
            sys.stdout.write("No plugins in the settings file.\n")
        for spec in specs:
            state = "approved" if plugins.approved(spec) else "not approved"
            sys.stdout.write(f"{spec.name}: {state}; tools {', '.join(spec.tools)}\n")
        return 0
    chosen = next((s for s in specs if s.name == name), None)
    if chosen is None:
        sys.stderr.write(f"claude-voice: no plugin {name!r} in the settings file\n")
        return 1
    spec = chosen
    sys.stdout.write(
        f"Plugin {spec.name}\n  runs: {' '.join([spec.command, *spec.args])}\n  in:   {spec.cwd}\n"
        f"  the voice may call: {', '.join(spec.tools)}\n"
    )
    try:
        typed = input(f"Type {spec.name} to approve it, anything else to stop: ")
    except EOFError:  # run with nothing to read from, such as from inside another program
        sys.stdout.write("\nNot approved: no answer was typed. Run it in a terminal.\n")
        return 1
    # RULE: the owner approves a plugin by typing its name
    if typed.strip() != spec.name:
        sys.stdout.write("Not approved.\n")
        return 1
    listing = plugins.Plugin(spec, plugins.CLIENT).listing()
    if not listing:
        sys.stdout.write(f"{spec.name} did not start, so nothing was approved. Check its command and folder.\n")
        return 1
    plugins.approve(spec, listing)
    for tool in spec.tools:
        found = listing.get(tool)
        sys.stdout.write(f"  {tool} takes: {plugins.takes(found['schema'])}\n" if found else f"  {tool}: not in it\n")
    sys.stdout.write(f"Approved. A running bridge offers {spec.name}'s tools within a few seconds.\n")
    return 0


def agent_command(action: str, env: Mapping[str, str], sessions: bool = False) -> int:
    """On another machine: show a pairing code for the hub, or serve it over the tailnet."""
    import socket

    from . import agent
    from . import config as settings

    home = Path(env.get("HOME") or Path.home())
    pairing = agent.Pairing(home / ".config/claude-voice/agent")
    machine = socket.gethostname().split(".")[0]
    if action == "install":
        command = [sys.executable, "-m", "claude_voice.cli", "agent"]
        return agent.install(home, command, sessions, run=_systemctl, login=claude_code_login, machine=machine)
    if action == "pair":
        code = pairing.start()
        sys.stdout.write(
            f"Pairing code: {code}\n  open for {agent.CODE_SECONDS // 60} minutes, once.\n"
            f"  On the hub, run: claude-voice machines approve {machine}\n"
        )
        return 0
    try:
        agent.serve(machine, settings.load(settings.FILE, env=env), pairing)
    except (agent.AgentError, settings.ConfigError) as exc:
        sys.stderr.write(f"claude-voice: {exc}\n")
        return 1
    return 0


def machines_command(action: str, name: str | None, env: Mapping[str, str]) -> int:
    """List the machines in the settings file, or pair with one: Roy types its code and name."""
    import asyncio

    from . import config as settings
    from . import machines

    try:
        specs = settings.load(settings.FILE, env=env).machines
    except settings.ConfigError as exc:
        sys.stderr.write(f"claude-voice: {exc}\n")
        return 1
    folder = Path(env.get("HOME") or Path.home()) / ".config/claude-voice"
    if action == "list":
        if not specs:
            sys.stdout.write("No machines in the settings file.\n")
        for spec in specs:
            sys.stdout.write(f"{spec.name} at {spec.address}: {machines.state(spec, folder)}\n")
        return 0
    chosen = next((s for s in specs if s.name == name), None)
    if chosen is None:
        sys.stderr.write(f"claude-voice: no machine {name!r} in the settings file\n")
        return 1
    return 0 if asyncio.run(machines.pair(chosen, folder=folder))["approved"] else 1


def permissions_command(folders: list[Path], dry_run: bool, env: Mapping[str, str]) -> int:
    """Add the permission rules to each repository's local settings, keeping the owner's own."""
    from . import config as settings
    from . import permissions, roles

    if not folders:
        try:
            folders = permissions.folders(settings.load(settings.FILE, own_clone=roles.own_clone(), env=env))
        except settings.ConfigError as exc:
            sys.stderr.write(f"claude-voice: {exc}\n")
            return 1
    if not folders:
        sys.stderr.write(f"claude-voice: no clone was found; name the folders, or set [paths] in {settings.FILE}\n")
        return 1
    rules, code = permissions.load(), 0
    for folder in folders:
        try:
            sys.stdout.write(permissions.apply(folder, rules, dry_run).line() + "\n")
        except permissions.PermissionsError as exc:
            sys.stderr.write(f"claude-voice: {exc}\n")
            code = 1
    return code


def _systemctl(command: list[str]) -> None:
    subprocess.run(command, check=True, timeout=30)


def entry() -> None:
    sys.exit(main())


if __name__ == "__main__":
    entry()
