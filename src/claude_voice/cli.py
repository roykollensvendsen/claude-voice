"""Command line for claude-voice: serve the bridge, or check its configuration."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

from . import envfile, invocation, procs, restarts
from .config import STATE_DIR
from .server import Config, ConfigError, load_config, serve_forever

if TYPE_CHECKING:
    from .config import PluginSpec

#: Every verb this command has. The documentation test requires each one to
#: appear in a worked example, so a verb cannot be added without one.
COMMANDS: tuple[str, ...] = (
    "serve",
    "check",
    "plugins",
    "home",
    "agent",
    "machines",
    "permissions",
    "org",
    "restart",
    "setup",
    "repos",
    "roles",
    "set",
)

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


def _config_check_name(text: str) -> str:
    """The check a configuration error belongs to, by the setting its message starts with."""
    for prefix, name in (
        ("ANTHROPIC_API_KEY", "api_key"),
        ("CLAUDE_VOICE_ROOT", "project_root"),
        ("CLAUDE_VOICE_TOKEN", "secret"),
    ):
        if text.startswith(prefix):
            return name
    return "configuration"


def _env_file_unread(env: Mapping[str, str]) -> list[envfile.Problem]:
    """No problems, because the service's env file is not opened at all."""
    return []


def checks(
    env: Mapping[str, str],
    transport: str,
    login: Callable[[], tuple[bool, str]],
    env_file: Callable[[Mapping[str, str]], list[envfile.Problem]] = envfile.read,
) -> tuple[Config | None, list[dict[str, Any]]]:
    """Every check, in the order a person would fix them, each with the step that fixes it.

    `env_file` reads the service's env file for its warnings; `setup --check` passes
    `_env_file_unread`, so that file is never opened.
    """
    found: list[dict[str, Any]] = []

    def add(name: str, ok: bool, detail: str, fix: str = "", required: bool = True) -> None:
        found.append({"name": name, "ok": ok, "required": required, "detail": detail, "fix": "" if ok else fix})

    cfg = None
    try:
        cfg = load_config(env, transport)
        add("configuration", True, "the configuration is usable")
    except ConfigError as exc:
        text = str(exc)
        detail, _, fix = text.partition(". ")
        add(_config_check_name(text), False, detail, fix)
    names = {c["name"] for c in found}
    if "project_root" not in names:
        root = Path(env.get("CLAUDE_VOICE_ROOT", "~/src")).expanduser()
        add("project_root", root.is_dir(), f"projects under {root}", f"Make it with `mkdir -p {root}`.")
    if transport == "http" and "secret" not in names:
        token = env.get("CLAUDE_VOICE_TOKEN", "")
        fix = f"Set CLAUDE_VOICE_TOKEN; see `{invocation.command()} check`."
        add("secret", len(token) >= 32, "a login secret is set", fix)
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
    for problem in env_file(env):
        # RULE: a misread env file line is a warning and never stops the check
        add("env_file", False, problem.detail, problem.fix, required=False)
    return cfg, found


def main(
    argv: list[str] | None = None,
    env: Mapping[str, str] = os.environ,
    serve: Callable[[Config], None] = serve_forever,
    login: Callable[[], tuple[bool, str]] = claude_code_login,
    systemctl: Callable[[list[str]], int] = restarts.systemctl,
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
    plugin_cmd = sub.add_parser(
        "plugins", help="list plugins, install one from its checkout, approve one by typing its name, or revoke one"
    )
    plugin_cmd.add_argument(
        "action", nargs="?", choices=["list", "install", "approve", "revoke", "set"], default="list"
    )
    plugin_cmd.add_argument("name", nargs="?")
    plugin_cmd.add_argument("values", nargs="*", help="with set: the key and its new value")
    plugin_cmd.add_argument("--from", dest="source", help="with install: the plugin's checkout (default ~/<its repo>)")
    plugin_cmd.add_argument("--dry-run", action="store_true", help="with install: show each change, write nothing")
    plugin_cmd.add_argument("--anywhere", action="store_true", help="with install: allow a home outside Norway")
    home_cmd = sub.add_parser("home", help="set the home position plugins share, typed without showing it")
    home_cmd.add_argument("action", nargs="?", choices=["set"], default="set")
    home_cmd.add_argument("--anywhere", action="store_true", help="allow a position outside Norway")
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
    machine_cmd.add_argument("action", nargs="?", choices=["list", "approve", "add"], default="list")
    machine_cmd.add_argument("name", nargs="?")
    machine_cmd.add_argument("values", nargs="*", help="with add: the machine's address")
    roles_cmd = sub.add_parser("roles", help="add one of your own roles, change one field of a role, or take one out")
    roles_cmd.add_argument("action", choices=["add", "set", "remove"])
    roles_cmd.add_argument("name")
    roles_cmd.add_argument(
        "fields", nargs="*", help="with add: key=value, such as folder=~/demo; with set: the key and its value"
    )
    set_cmd = sub.add_parser("set", help="set one of the settings that are not run, such as voice_instructions_file")
    set_cmd.add_argument("key")
    set_cmd.add_argument("value")
    permission_cmd = sub.add_parser("permissions", help="give Claude Code the voice repositories' permission rules")
    permission_cmd.add_argument("folders", nargs="*", type=Path, help="default: the clones in the settings file")
    permission_cmd.add_argument("--dry-run", action="store_true", help="say what would be added, and write nothing")
    permission_cmd.add_argument("--check", action="store_true", help="run each repository's session hook once")
    permission_cmd.add_argument(
        "--accept", action="store_true", help="show the repositories' rights files, and accept them by typing once"
    )
    permission_cmd.add_argument(
        "--all", action="store_true", help="with --accept: every repository of the bridge's own programme"
    )
    permission_cmd.add_argument(
        "--shipped",
        action="store_true",
        help="after an update: write the shipped rules where that only takes rights away, and say what waits",
    )
    org_cmd = sub.add_parser("org", help="print the org chart, or check its files")
    org_cmd.add_argument("action", nargs="?", choices=["show", "check"], default="show")
    restart_cmd = sub.add_parser("restart", help="restart the bridge's service, noting why for its health answer")
    restart_cmd.add_argument("--why", default="", help="what the restart is for, shown in health")
    restart_cmd.add_argument("--dry-run", action="store_true", help="say what it would do, and do nothing")
    setup_cmd = sub.add_parser("setup", help="set up the platform; --check reports each step and changes nothing")
    setup_cmd.add_argument("--check", action="store_true", help="say what is done, missing or yours to run")
    setup_cmd.add_argument("--json", action="store_true", help="answer in JSON, for an assistant doing the setup")
    repos_cmd = sub.add_parser("repos", help="the profile's repositories: where each one stands")
    repos_cmd.add_argument("action", choices=["sync"])
    repos_cmd.add_argument("--check", action="store_true", help="say what a sync would do, from local refs only")
    parsed = parser.parse_args(args)

    verbs: dict[str, Callable[[], int]] = {
        "setup": lambda: setup_command(parsed.check, parsed.json, env, login),
        "repos": lambda: repos_command(parsed.check, env),
        "restart": lambda: restart_command(parsed.why, parsed.dry_run, systemctl),
        "permissions": lambda: permissions_verb(parsed, env),
        "org": lambda: org_command(parsed.action, env),
        "machines": lambda: _machines(parsed, env),
        "roles": lambda: _settings_edit(f"roles {parsed.action}", [parsed.name, *parsed.fields], env),
        "set": lambda: _settings_edit("set", [parsed.key, parsed.value], env),
        "agent": lambda: agent_command(parsed.action, env, parsed.sessions),
        "plugins": lambda: plugins_command(parsed.action, parsed.name, env, parsed),
        "home": lambda: home_command(parsed.anywhere, env),
        "check": lambda: check_command(env, parsed.transport, parsed.json, login),
    }
    if parsed.command in verbs:
        return verbs[parsed.command]()

    try:
        cfg = load_config(env, parsed.transport)
    except ConfigError as exc:
        sys.stderr.write(f"claude-voice: {exc}\n")
        return 1
    serve(cfg)
    return 0


def _settings_edit(verb: str, words: list[str | None], env: Mapping[str, str]) -> int:
    from . import settings_edit

    return settings_edit.command(verb, [w for w in words if w], env)


def restart_command(why: str, dry_run: bool, systemctl: Callable[[list[str]], int]) -> int:
    """Note why the bridge is restarted, then restart its service."""
    command = " ".join(restarts.RESTART)
    said = f' "{why}"' if why else ""
    if dry_run:
        sys.stdout.write(f"claude-voice: would note a planned restart{said}, then run: {command}\n")
        return 0
    # RULE: claude voice restart notes why before it restarts
    try:
        restarts.note(STATE_DIR, "planned", "cli", text=why or None)
    except OSError as exc:
        sys.stderr.write(f"claude-voice: could not note why ({exc}); restarting anyway, so health will say unplanned\n")
    code = systemctl(restarts.RESTART)
    if code:
        status = f"systemctl --user status {restarts.UNIT}"
        sys.stderr.write(f"claude-voice: `{command}` failed. Run `{status}` to see why.\n")
    return code


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


NOT_BUILT = (
    "claude-voice: only the check is built so far. "
    "Run `{command} --check`: it says what is left, and changes nothing.\n"
)


def _where(value: str, home: Path) -> Path:
    return Path(str(home) + value[1:] if value.startswith("~") else value)


def _platform(env: Mapping[str, str]) -> tuple[Any, Any, list[Any], Path] | str:
    """The settings, the merged profile and its folders as they stand, or what is wrong and its fix."""
    from . import config as settings
    from . import profiles, reposync, roles

    home = Path(env.get("HOME") or Path.home())
    try:
        cfg = settings.load(settings.FILE, own_clone=roles.own_clone(), env=env)
        profile = profiles.load(profiles.layers(cfg.profile, home))
    except (settings.ConfigError, profiles.ProfileError) as exc:
        return f"claude-voice: {exc}"
    known = {Path(p.cwd).name: Path(p.cwd) for p in cfg.plugins}
    for name, path in (
        ("claude-voice", cfg.paths.claude_voice),
        ("claude-voice-ui", cfg.paths.claude_voice_ui),
        ("claude-voice-mobile", cfg.paths.claude_voice_mobile),
    ):
        if path is not None:
            known[name] = path
    parent = _where(env.get("CLAUDE_VOICE_ROOT", "~/src"), home)
    return cfg, profile, reposync.check(profile.repos.values(), parent, known), home


def setup_command(check: bool, as_json: bool, env: Mapping[str, str], login: Callable[[], tuple[bool, str]]) -> int:
    """Report every setup step as done, missing or the person's own to run; change nothing."""
    from dataclasses import asdict

    from . import setup

    if not check:
        sys.stderr.write(NOT_BUILT.format(command="claude-voice setup"))
        return 2
    state = _platform(env)
    if isinstance(state, str):
        sys.stderr.write(state + "\n")
        return 1
    cfg, profile, folders, home = state
    # RULE: setup never opens the service's env file
    _, bridge = checks(env, "stdio", login, env_file=_env_file_unread)  # it holds the login secret
    found = setup.steps(cfg, env, home, bridge, profile, folders, procs.find)
    left = setup.missing(found)
    if as_json:
        report = {"ready": not left, "missing": [s.name for s in left], "steps": [asdict(s) for s in found]}
        sys.stdout.write(json.dumps(report, indent=2) + "\n")
    else:
        sys.stdout.write("\n".join(setup.lines(found)) + "\n")
    return 1 if left else 0


def repos_command(check: bool, env: Mapping[str, str]) -> int:
    """Say where each of the profile's repositories stands, and what a sync would do with it."""
    if not check:
        sys.stderr.write(NOT_BUILT.format(command="claude-voice repos sync"))
        return 2
    state = _platform(env)
    if isinstance(state, str):
        sys.stderr.write(state + "\n")
        return 1
    for folder in state[2]:
        sys.stdout.write(folder.line() + "\n")
    return 0


def _plugin_state(spec: PluginSpec) -> str:
    """Whether a plugin is approved, checked the way a restart checks it: started, and its
    tools and words for the voice compared with the approval."""
    from . import plugins

    _, _, why = plugins.check_one(spec)
    # RULE: plugins list says approved only when a restart would offer it
    if why == "started":
        return "approved"
    if why.startswith("not started: "):
        return f"approved, but it was not started: {why.removeprefix('not started: ')}"
    if why == "not started":
        return "approved, but it did not start; check its command and folder"
    return f"{why}; to approve it, run: {invocation.command()} plugins approve {spec.name}"


def _list_plugins(specs: list[PluginSpec]) -> int:
    """Each plugin in the settings file, and whether a restart would offer it."""
    if not specs:
        sys.stdout.write("No plugins in the settings file.\n")
    for spec in specs:
        sys.stdout.write(f"{spec.name}: {_plugin_state(spec)}; tools {', '.join(spec.tools)}\n")
    return 0


def _show_approved(spec: PluginSpec, listing: dict[str, dict[str, Any]]) -> None:
    """What the owner just approved: each tool's inputs and every word the voice reads."""
    from . import plugins

    for tool in spec.tools:
        found = listing.get(tool)
        sys.stdout.write(f"  {tool} takes: {plugins.takes(found['schema'])}\n" if found else f"  {tool}: not in it\n")
        if found and found.get("description"):
            sys.stdout.write(f"    the voice reads: {found['description']}\n")
    rules = plugins.instructions(listing)
    if rules:
        sys.stdout.write(f"  {spec.name}'s rules for the voice: {rules}\n")


def plugins_command(action: str, name: str | None, env: Mapping[str, str], options: Any = None) -> int:
    """List the plugins in the settings file, install one, approve one (the owner types its
    name), or take an approval back."""
    from . import config as settings

    if action == "revoke":
        return _revoke(name)
    if action == "set":
        from . import settings_edit

        return settings_edit.command("plugins set", [w for w in (name, *options.values) if w], env)
    if action == "install":
        return install_command(name, options, env)
    try:
        specs = settings.load(settings.FILE, env=env).plugins
    except settings.ConfigError as exc:
        sys.stderr.write(f"claude-voice: {exc}\n")
        return 1
    if action == "list":
        return _list_plugins(specs)
    chosen = next((s for s in specs if s.name == name), None)
    if chosen is None:
        sys.stderr.write(f"claude-voice: no plugin {name!r} in the settings file\n")
        return 1
    return _approve(chosen)


def install_command(name: str | None, options: Any, env: Mapping[str, str]) -> int:
    """Add a plugin from its checkout step by step, then ask for the typed approval."""
    import getpass

    from . import install

    # RULE: plugins install asks only in a real terminal
    if _refuse_outside_a_terminal("plugins install", env):
        return 1
    if not name:
        sys.stderr.write(
            "claude-voice: say which plugin, such as: claude-voice plugins install fuel --from ~/fuel-mcp\n"
        )
        return 1
    try:
        plan = install.plan(name, options.source, env)
    except install.InstallError as exc:
        sys.stderr.write(f"claude-voice: {exc}.\n")
        return 1
    outcome = install.run(plan, install.Asker(_say, getpass.getpass, input), options.dry_run, options.anywhere)
    code = 1 if outcome.stopped else _install_approval(plan, outcome, options.dry_run, env)
    if outcome.error:
        sys.stderr.write(f"claude-voice: {outcome.error}.\n")
    for line in install.summary(outcome, plan, options.dry_run):
        _say(line)
    return code


def _install_approval(plan: Any, outcome: Any, dry_run: bool, env: Mapping[str, str]) -> int:
    """The last step: plugins approve's own typed approval, unless it is still valid."""
    from . import config as settings
    from . import install, plugins

    name, number = plan.manifest.name, outcome.steps + 1
    if dry_run:
        _say(f"{number}. Approve {name}: would ask you to type {name}.")
        return 0
    spec = next(s for s in settings.load(settings.FILE, env=env).plugins if s.name == name)
    if plugins.check_one(spec)[2] == "started":
        _say(f"{number}. Approve {name}: already approved.")
        return 0
    _say(f"{number}. Approve {name}:")
    code = _approve(spec)
    if code == 0:
        outcome.changes.append(install.Change("approved", name, f"claude-voice plugins revoke {name}"))
    return code


def _revoke(name: str | None) -> int:
    """Take a plugin's approval back; it needs typing again before the voice gets its tools."""
    from . import plugins

    if not name:
        sys.stderr.write("claude-voice: say which plugin, such as: claude-voice plugins revoke garage\n")
        return 1
    if plugins.revoke(name):
        sys.stdout.write(
            f"{name} is no longer approved. A running bridge stops offering its tools within a few "
            f"seconds. To approve it again: claude-voice plugins approve {name}\n"
        )
    else:
        sys.stdout.write(f"{name} was not approved, so nothing changed.\n")
    return 0


def _approve(spec: PluginSpec) -> int:
    """Show what the plugin runs and approve it when the owner types its name."""
    from . import plugins

    sys.stdout.write(
        f"Plugin {spec.name}\n  runs: {' '.join([spec.command, *spec.args])}\n  in:   {spec.cwd}\n"
        f"  the voice may call: {', '.join(spec.tools)}\n"
    )
    try:
        # RULE: the approval prompt says the plugin starts after its name is typed
        typed = input(
            f"Type {spec.name} to approve it (this starts it once to read its tools), anything else to stop: "
        )
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
    _show_approved(spec, listing)
    sys.stdout.write(f"Approved. A running bridge offers {spec.name}'s tools within a few seconds.\n")
    return 0


def _terminal(env: Mapping[str, str]) -> bool:
    """Whether the owner is at a terminal of their own to answer questions."""
    from . import fields

    return fields.in_terminal(env, sys.stdin, sys.stdout)


def _refuse_outside_a_terminal(verb: str, env: Mapping[str, str]) -> bool:
    """Say why a command that asks questions will not run here, before it reads anything."""
    from . import fields

    if _terminal(env):
        return False
    sys.stderr.write(f"claude-voice: {fields.NOT_A_TERMINAL.format(verb=verb)}.\n")
    return True


def _say(line: str) -> None:
    sys.stdout.write(line + "\n")


def home_command(anywhere: bool, env: Mapping[str, str]) -> int:
    """Ask for the home position again and save it for every plugin that reads it."""
    import getpass

    from . import home, safefile

    if _refuse_outside_a_terminal("home set", env):
        return 1
    try:
        home.set_position(Path(env.get("HOME") or Path.home()), getpass.getpass, _say, anywhere)
    except (EOFError, KeyboardInterrupt):
        sys.stdout.write("\nStopped; nothing was changed.\n")
        return 1
    except (safefile.Unsafe, OSError) as exc:
        sys.stderr.write(f"claude-voice: {exc}. Nothing was changed.\n")
        return 1
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


def _machines(parsed: Any, env: Mapping[str, str]) -> int:
    """machines add writes the settings file; every other machines action is machines_command."""
    if parsed.action == "add":
        return _settings_edit("machines add", [parsed.name, *parsed.values], env)
    return machines_command(parsed.action, parsed.name, env)


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


def _the_clones(folders: list[Path], env: Mapping[str, str]) -> list[Path]:
    """The folders named, or else the clones in the settings; none, said on stderr, when
    there are none or the settings cannot be read."""
    from . import config as settings
    from . import permissions, roles

    if folders:
        return folders
    try:
        found = permissions.folders(settings.load(settings.FILE, own_clone=roles.own_clone(), env=env))
    except settings.ConfigError as exc:
        sys.stderr.write(f"claude-voice: {exc}\n")
        return []
    if not found:
        sys.stderr.write(f"claude-voice: no clone was found; name the folders, or set [paths] in {settings.FILE}\n")
    return found


def permissions_command(folders: list[Path], dry_run: bool, env: Mapping[str, str]) -> int:
    """Add the permission rules to each repository's local settings, keeping the owner's own."""
    from . import permissions

    folders = _the_clones(folders, env)
    if not folders:
        return 1
    rules, code = permissions.load(), 0
    backups = permissions.backup_root(Path(env.get("HOME") or Path.home()))
    hooks = _session_hooks(folders, env)
    for folder in folders:
        try:
            outcome = permissions.apply(folder, rules, dry_run, backup_root=backups, hook=hooks[folder])
            sys.stdout.write(outcome.line() + "\n")
        except permissions.PermissionsError as exc:
            sys.stderr.write(f"claude-voice: {exc}\n")
            code = 1
    return code


def _session_hooks(folders: list[Path], env: Mapping[str, str]) -> dict[Path, str]:
    """The session hook's command for each folder: its programme's repositories from the org
    chart, and the owner's terms file from the bridge's own settings."""
    from . import config as settings
    from . import org, permissions, roles

    own = roles.own_clone()
    try:
        terms = settings.load(settings.FILE, own_clone=own, env=env).personal_terms_file
    except settings.ConfigError:
        terms = None
    root = Path(env.get("CLAUDE_VOICE_ROOT", "~/src")).expanduser()
    chart = org.load(org.known_folders(root, [own, *folders]))
    return {
        folder: permissions.hook_command(org.repositories(chart.place(str(folder.resolve()), ()).programme), terms)
        for folder in folders
    }


def hook_check_command(folders: list[Path], env: Mapping[str, str]) -> int:
    """Run the session hook written in each repository once, and say whether it answers."""
    from . import permissions, session_hook

    folders = _the_clones(folders, env)
    if not folders:
        return 1
    code = 0
    for folder in folders:
        try:
            written = permissions.written_hook(permissions._read(folder / permissions.SETTINGS))
        except permissions.PermissionsError:
            written = None
        found = session_hook.self_check(shlex.split(written)) if written else None
        if found and found["ok"]:
            sys.stdout.write(f"{folder}: the session hook answers\n")
            continue
        detail = found["detail"] if found else "no session hook is written"
        sys.stdout.write(f"{folder}: {detail}. Fix: run `claude-voice permissions` to write it again.\n")
        code = 1
    return code


def permissions_verb(parsed: argparse.Namespace, env: Mapping[str, str]) -> int:
    if parsed.check:
        return hook_check_command(parsed.folders, env)
    if parsed.shipped:
        return shipped_command(parsed.folders, env)
    if parsed.accept:
        return accept_command(parsed.folders, env, parsed.all)
    return permissions_command(parsed.folders, parsed.dry_run, env)


def shipped_command(folders: list[Path], env: Mapping[str, str]) -> int:
    """Bring the shipped rules to the repositories where that only takes rights away, and say
    for each what was written, what waits for the owner's accept, and what was left alone."""
    from . import config as settings
    from . import org, permissions, roles, shipped

    own = roles.own_clone()
    if not folders:
        try:
            folders = permissions.folders(settings.load(settings.FILE, own_clone=own, env=env))
        except settings.ConfigError as exc:
            sys.stderr.write(f"claude-voice: {exc}\n")
            return 1
    root = Path(env.get("CLAUDE_VOICE_ROOT", "~/src")).expanduser()
    chart = org.load(org.known_folders(root, [own, *folders]))
    backups = permissions.backup_root(Path(env.get("HOME") or Path.home()))
    # RULE: the shipped rules are recorded under the commit that is installed
    sha = shipped.installed_commit(own)
    done = shipped.sync(folders, chart, own, permissions.load(), sha, backup_root=backups)
    for line in shipped.report(done):
        sys.stdout.write(line + "\n")
    return 0


#: The terminal itself, which a pipe into stdin can't stand in for.
TERMINAL = "/dev/tty"


def _read_from_terminal(prompt: str) -> str:
    """One line typed at the terminal itself, never from a pipe into stdin. A terminal can't
    seek, and a file opened "r+" in text mode needs to, so it is opened once each way."""
    # RULE: the typed answer is read from a real terminal
    with open(TERMINAL, "w", encoding="utf-8") as out, open(TERMINAL, encoding="utf-8") as typed:
        out.write(prompt)
        out.flush()
        return typed.readline()


def _own_programme_repos(env: Mapping[str, str]) -> list[Path]:
    """The repositories in the settings that the org chart places in the bridge's own
    programme; none when the chart cannot be read. Never guessed from a folder's name."""
    from . import config as settings
    from . import org, permissions, roles

    own = roles.own_clone()
    folders = permissions.folders(settings.load(settings.FILE, own_clone=own, env=env))
    root = Path(env.get("CLAUDE_VOICE_ROOT", "~/src")).expanduser()
    chart = org.load(org.known_folders(root, [own, *folders]))
    mine = next((p for p in chart.programmes if own and p.repo == own.resolve()), None)
    # RULE: accept all says so when the chart cannot name the bridges own programme
    if mine is None:
        raise settings.ConfigError("the org chart names no programme for the bridge's own clone, so --all finds none")
    return [f for f in folders if chart.place(str(f.resolve()), ()).programme is mine]


def _accept_summary(repo: Path, shown: list[Any]) -> str:
    from . import accepted

    lines = [line for item in shown for line in item.lines]
    widen = sum(1 for line in lines if line.kind == accepted.WIDENS)
    files = f"{len(shown)} file{'' if len(shown) == 1 else 's'}"
    return f"{repo}: {files}, {widen} WIDENS, {len(lines) - widen} narrows"


def _accept_folders(folders: list[Path], env: Mapping[str, str], every: bool) -> list[Path] | None:
    """The repositories to accept, or None after saying why there are none or why not here."""
    from . import config as settings

    if every:
        try:
            folders = _own_programme_repos(env)
        except settings.ConfigError as exc:
            sys.stderr.write(f"claude-voice: {exc}\n")
            return None
    if not folders:
        sys.stderr.write("claude-voice: name the repositories to accept, or add --all\n")
        return None
    if sys.platform == "win32":
        sys.stderr.write("claude-voice: the accept reads a terminal of its own; run it inside WSL\n")
        return None
    # RULE: an accept is typed in a terminal never piped
    if _refuse_outside_a_terminal("permissions --accept", env):
        return None
    return folders


def _show_for_accept(found: list[tuple[Path, list[Any]]]) -> None:
    """One line per repository, then every widening repository's lines in full."""
    from . import accepted

    for repo, shown in found:
        sys.stdout.write(_accept_summary(repo, shown) + "\n")
    for _repo, shown in found:
        # RULE: every line that widens a sessions rights is shown before the owner types
        if any(line.kind == accepted.WIDENS for item in shown for line in item.lines):
            sys.stdout.write(accepted.text(shown))


def _typed_yes(found: list[tuple[Path, list[Any]]]) -> bool:
    """Whether the owner typed the one answer that accepts everything shown."""
    answer = found[0][0].name if len(found) == 1 else f"accept {len(found)}"
    typed = _read_from_terminal(
        f"Read the WIDENS lines first. Type {answer} to accept all of this, anything else to stop: "
    )
    # RULE: the owner accepts a repositorys rights by typing its name
    return typed.strip() == answer


def _record_accept(every_file: list[Any]) -> int:
    """Record the files shown, unless any of them moved since."""
    from . import accepted

    # RULE: one file changed after it was shown refuses the whole accept
    moved = [str(item.path) for item in every_file if not item.path.is_file() or item.path.read_bytes() != item.body]
    if moved:
        sys.stderr.write(
            f"claude-voice: {', '.join(moved)} changed after it was shown; nothing was accepted. Run it again.\n"
        )
        return 1
    try:
        accepted.accept(every_file)
    except accepted.Changed as exc:
        sys.stderr.write(f"claude-voice: {exc}\n")
        return 1
    return 0


def accept_command(folders: list[Path], env: Mapping[str, str], every: bool = False) -> int:
    """Show what changed in the repositories' rights files since the owner last accepted them,
    and record them all once the owner types one answer. Every line that widens a session's
    rights is shown in full; a repository that only narrows gets one line."""
    from . import accepted

    chosen = _accept_folders(folders, env, every)
    if chosen is None:
        return 1
    found = [(repo.resolve(), accepted.show(repo.resolve())) for repo in chosen]
    found = [(repo, shown) for repo, shown in found if shown]
    if not found:
        sys.stdout.write("Nothing to accept; every rights file is as last accepted.\n")
        return 0
    _show_for_accept(found)
    if not _typed_yes(found):
        sys.stdout.write("Not accepted.\n")
        return 1
    every_file = [item for _, shown in found for item in shown]
    if _record_accept(every_file):
        return 1
    files = f"{len(every_file)} file{'' if len(every_file) == 1 else 's'}"
    repos = f"{len(found)} repositor{'y' if len(found) == 1 else 'ies'}"
    sys.stdout.write(f"Accepted {files} in {repos}.\n")
    return 0


def org_command(action: str, env: Mapping[str, str]) -> int:
    """Print the org chart as the voice hears it, or check its files (docs/org-chart.md)."""
    from . import config as settings
    from . import events, org, roles

    home = Path(env.get("HOME") or Path.home())
    try:
        cfg = settings.load(settings.FILE, own_clone=roles.own_clone(), env=env)
    except settings.ConfigError as exc:
        sys.stderr.write(f"claude-voice: {exc}\n")
        return 1
    root = Path(env.get("CLAUDE_VOICE_ROOT", "~/src")).expanduser()
    chart = org.load(org.known_folders(root, [cfg.paths.claude_voice, *(p.cwd for p in cfg.plugins)]))
    live = events.read_live(home / ".claude" / "sessions")
    if action == "show":
        for line in org.overview(chart, live, chart.placer()) or ["No programme has an org file."]:
            sys.stdout.write(line + "\n")
        return 0
    running = {str(d.get("name")) for d in live}
    for line in org.check(chart, running):
        sys.stdout.write(line + "\n")
    return 1 if chart.problems else 0


def _systemctl(command: list[str]) -> None:
    subprocess.run(command, check=True, timeout=30)


def entry() -> None:
    sys.exit(main())


if __name__ == "__main__":
    entry()
