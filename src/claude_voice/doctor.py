"""The doctor: which of the bridge's dependencies are missing, what each costs, and the fix.

Only signing in to Claude Code is required; without the rest the bridge still runs,
but loses something the check names in `affects`. A contribute session needs more,
and does not start without it (`before_role`).

Everything the doctor asks of the computer goes through a `Host`, so the checks are
the same in the tests and for real. Nothing here prints a secret.
"""

from __future__ import annotations

import json
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from . import procs
from .config import STATE_DIR

STATUSLINE_FILE = "statusline"  # a folder of one file per session
STATUSLINE_FRESH = 6 * 3600


class Host(Protocol):
    def which(self, name: str) -> str | None: ...
    def run(self, *command: str) -> tuple[int, str]: ...
    def login(self) -> tuple[bool, str]: ...
    def trusted(self, folder: Path) -> bool: ...


class RealHost:
    """This computer."""

    def which(self, name: str) -> str | None:
        return procs.find(name)

    def run(self, *command: str) -> tuple[int, str]:
        path = self.which(command[0])
        if not path:
            return 127, ""
        try:
            out = subprocess.run([path, *command[1:]], capture_output=True, text=True, timeout=20, check=False)
        except (OSError, subprocess.SubprocessError):
            return 1, ""
        return out.returncode, out.stdout

    def login(self) -> tuple[bool, str]:
        from .cli import claude_code_login

        return claude_code_login()

    def trusted(self, folder: Path) -> bool:
        """Whether Claude Code has been told it may work in `folder` (its trust question)."""
        try:
            config = json.loads(Path("~/.claude.json").expanduser().read_text())
        except (OSError, ValueError):
            return False
        project = (config.get("projects") or {}).get(str(folder.resolve())) or {}
        return bool(project.get("hasTrustDialogAccepted"))


def check(
    id: str,
    title: str,
    ok: bool,
    detail: str,
    *,
    required: bool = False,
    affects: str = "",
    fix: str = "",
    fix_url: str | None = None,
) -> dict[str, Any]:
    return {
        "id": id,
        "title": title,
        "ok": ok,
        "required": required,
        "affects": affects,
        "detail": detail,
        "fix": "" if ok else fix,
        "fix_url": None if ok else fix_url,
    }


def _tool(host: Host, name: str, title: str, affects: str, fix: str, url: str) -> dict[str, Any]:
    found = host.which(name)
    return check(
        name,
        title,
        bool(found),
        f"{name} is at {found}" if found else f"{name} is not installed",
        affects=affects,
        fix=fix,
        fix_url=url,
    )


def _gh(host: Host) -> dict[str, Any]:
    affects = "pull requests on the cards, and the contribute session"
    if not host.which("gh"):
        return check(
            "gh",
            "GitHub CLI",
            False,
            "gh is not installed",
            affects=affects,
            fix="Install it from https://cli.github.com, then run: gh auth login",
            fix_url="https://cli.github.com",
        )
    code, _ = host.run("gh", "auth", "status")
    return check(
        "gh",
        "GitHub CLI",
        code == 0,
        "gh is installed and signed in" if code == 0 else "gh is installed but not signed in",
        affects=affects,
        fix="gh auth login",
        fix_url="https://cli.github.com/manual/gh_auth_login",
    )


def _tailscale(host: Host) -> dict[str, Any]:
    affects = "reaching the bridge from the phone"
    if not host.which("tailscale"):
        return check(
            "tailscale",
            "Tailscale",
            False,
            "tailscale is not installed",
            affects=affects,
            fix="Install Tailscale and sign in: https://tailscale.com/download",
            fix_url="https://tailscale.com/download",
        )
    code, out = host.run("tailscale", "status", "--json")
    try:
        state = json.loads(out).get("BackendState") if code == 0 else None
    except ValueError:
        state = None
    return check(
        "tailscale",
        "Tailscale",
        state == "Running",
        f"Tailscale is {state or 'not answering'}",
        affects=affects,
        fix="sudo tailscale up",
        fix_url="https://tailscale.com/kb/1080/cli",
    )


def _serve_status(host: Host) -> dict[str, Any]:
    """What `tailscale serve` says it serves, or nothing when it cannot be read."""
    code, out = host.run("tailscale", "serve", "status", "--json")
    try:
        return json.loads(out) if code == 0 and out.strip() else {}
    except ValueError:
        return {}


def _web_ports(status: dict[str, Any], port: int, funnel: set[str]) -> list[str]:
    """The web ports, answered only by name, that lead to the bridge and Funnel does not open."""
    return [
        hostport
        for hostport, web in (status.get("Web") or {}).items()
        if hostport not in funnel
        and any(f"127.0.0.1:{port}" in str(h.get("Proxy", "")) for h in (web.get("Handlers") or {}).values())
    ]


def _tcp_ports(status: dict[str, Any], port: int, funnel: set[str]) -> list[str]:
    """The plain TCP forwards, which answer by address too, that lead to the bridge off Funnel."""
    funnel_ports = {h.rsplit(":", 1)[-1] for h in funnel}
    return [
        f"tcp port {listen}"
        for listen, tcp in (status.get("TCP") or {}).items()
        if str((tcp or {}).get("TCPForward", "")).endswith(f":{port}") and listen not in funnel_ports
    ]


def _tailnet_port(host: Host, port: int) -> dict[str, Any]:
    """A `tailscale serve` port, not open to Funnel, that leads to this bridge."""
    fix = (
        f"By name: tailscale serve --bg --https=8814 http://127.0.0.1:{port} ; "
        f"by address (when the phone cannot look the name up): tailscale serve --bg --tcp=8813 tcp://127.0.0.1:{port}"
    )
    affects = "the companion app on the phone"
    if not host.which("tailscale"):
        return check("tailnet_port", "Tailnet-only port", False, "tailscale is not installed", affects=affects, fix=fix)
    status = _serve_status(host)
    funnel = set((status.get("AllowFunnel") or {}).keys())
    ours = _web_ports(status, port, funnel) + _tcp_ports(status, port, funnel)
    return check(
        "tailnet_port",
        "Tailnet-only port",
        bool(ours),
        f"the bridge is served on the tailnet at {', '.join(sorted(ours))}"
        if ours
        else "no tailnet-only tailscale serve port leads to the bridge",
        affects=affects,
        fix=fix,
        fix_url="https://tailscale.com/kb/1312/serve",
    )


def _statusline(state_dir: Path) -> dict[str, Any]:
    path = state_dir.expanduser() / STATUSLINE_FILE
    try:
        newest = max((p.stat().st_mtime for p in path.glob("*.json")), default=None) if path.is_dir() else None
    except OSError:
        newest = None
    age = time.time() - newest if newest is not None else None
    fresh = age is not None and age < STATUSLINE_FRESH
    return check(
        "statusline",
        "Status line figures",
        fresh,
        "the status line wrote its figures lately"
        if fresh
        else f"{path} is missing"
        if age is None
        else f"{path} has not been written for {round(age / 3600)} hours",
        affects="the plan's five-hour and weekly bars, and how full each session is",
        fix=f"Make your Claude Code status line script also write its input to {path}/<session id>.json "
        "(see docs/event-api.md, Usage)",
        fix_url="https://code.claude.com/docs/en/statusline",
    )


def _role_clone(role: dict[str, Any]) -> tuple[Path | None, dict[str, Any]]:
    from .roles import folder_for

    try:
        folder = folder_for(role)
    except LookupError as exc:
        return None, check(
            "role_clone",
            "Your clone of claude-voice",
            False,
            str(exc),
            required=True,
            affects="the contribute session",
            fix="git clone https://github.com/roykollensvendsen/claude-voice",
        )
    is_git = (folder / ".git").exists()
    return folder, check(
        "role_clone",
        "Your clone of claude-voice",
        is_git,
        f"{folder} is a git clone" if is_git else f"{folder} is not a git clone",
        required=True,
        affects="the contribute session",
        fix=f"git clone https://github.com/{role.get('upstream', '')} {folder}",
    )


def _role_trust(host: Host, folder: Path) -> dict[str, Any]:
    ok = host.trusted(folder)
    return check(
        "role_trust",
        "Claude Code trusts the clone",
        ok,
        f"Claude Code may work in {folder}" if ok else f"Claude Code has not been allowed to work in {folder}",
        required=True,
        affects="the contribute session",
        fix=f"cd {folder} && claude, then answer yes when it asks whether to trust the folder",
    )


def examine(host: Host, role: dict[str, Any] | None, state_dir: Path = STATE_DIR, port: int = 8811) -> dict[str, Any]:
    """Every check, the required ones first."""
    signed_in, detail = host.login()
    checks = [
        check(
            "claude_signed_in",
            "Claude Code signed in",
            signed_in,
            detail,
            required=True,
            affects="everything: no session can work",
            fix="Run `claude`, type /login and sign in with your Claude account",
            fix_url="https://code.claude.com/docs/en/setup",
        ),
        _tool(
            host,
            "git",
            "Git",
            "git status on the cards, and the contribute session",
            "Install git with your package manager",
            "https://git-scm.com/downloads",
        ),
        _gh(host),
        _tool(
            host,
            "uv",
            "uv",
            "updating claude-voice, and running its tests",
            "curl -LsSf https://astral.sh/uv/install.sh | sh",
            "https://docs.astral.sh/uv/",
        ),
        _tailscale(host),
        _tailnet_port(host, port),
        _statusline(state_dir),
    ]
    if role is not None:
        folder, clone = _role_clone(role)
        checks.append({**clone, "required": False})
        if folder is not None:
            checks.append({**_role_trust(host, folder), "required": False})
    return {
        "ready": all(c["ok"] for c in checks if c["required"]),
        "checked_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "checks": checks,
    }


def before_role(host: Host, role: dict[str, Any]) -> list[dict[str, Any]]:
    """What stops a contribute session from working: nothing means it may start."""
    needed = [
        _tool(
            host,
            "git",
            "Git",
            "the contribute session",
            "Install git with your package manager",
            "https://git-scm.com/downloads",
        ),
        _gh(host),
        _tool(
            host,
            "uv",
            "uv",
            "the contribute session's tests",
            "curl -LsSf https://astral.sh/uv/install.sh | sh",
            "https://docs.astral.sh/uv/",
        ),
    ]
    folder, clone = _role_clone(role)
    needed.append(clone)
    if folder is not None and clone["ok"]:
        needed.append(_role_trust(host, folder))
    return [c for c in needed if not c["ok"]]
