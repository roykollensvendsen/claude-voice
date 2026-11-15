"""The text of what the release's install script writes: two units and one polkit rule.

They are made here, from code that is reviewed and tested, and never by the agent's own
code. The install script embeds them when a release is built.
"""

from __future__ import annotations

ROOT = "/usr/local/lib/claude-voice-power"
SERVICE = "claude-voice-power.service"
SOCKET_UNIT = "claude-voice-power.socket"
SOCKET_PATH = "/run/claude-voice-power/sock"
USER = "cv-power"
CLIENTS = "cv-power-clients"  # the group holding the user the agent runs as
ACTIONS = (
    "org.freedesktop.login1.power-off",
    "org.freedesktop.login1.power-off-multiple-sessions",
    "org.freedesktop.login1.reboot",
    "org.freedesktop.login1.reboot-multiple-sessions",
)


def service_text(dynamic: bool) -> str:
    """The helper's own system service. With polkit 121 or later it is a dynamic user, and the
    polkit rule names the unit; before that it is the fixed user cv-power."""
    who = "DynamicUser=yes" if dynamic else f"User={USER}"
    return (
        "[Unit]\n"
        "Description=claude-voice power helper: acts only on a yes the hub signed\n"
        f"Requires={SOCKET_UNIT}\nAfter={SOCKET_UNIT}\n\n"
        "[Service]\n"
        f"ExecStart={ROOT}/bin/python -I -m claude_voice_power\n"
        f"WorkingDirectory={ROOT}\n"
        f"{who}\n"
        "Environment=\n"
        "ProtectHome=yes\nNoNewPrivileges=yes\nProtectSystem=strict\nPrivateTmp=yes\n"
        "RestrictAddressFamilies=AF_UNIX\n"
        "Restart=on-failure\n"
    )


def socket_text() -> str:
    """Where the agent's user reaches the helper: one socket, for the clients group only."""
    return (
        "[Unit]\nDescription=claude-voice power helper socket\n\n"
        f"[Socket]\nListenStream={SOCKET_PATH}\nDirectoryMode=0755\n"
        f"SocketMode=0660\nSocketUser=root\nSocketGroup={CLIENTS}\n\n"
        "[Install]\nWantedBy=sockets.target\n"
    )


def polkit_rule(dynamic: bool) -> str:
    """The one rule: power to the helper alone, never to a user, a group or anyone else."""
    # RULE: only cv-power holds a power right
    who = f'subject.system_unit == "{SERVICE}"' if dynamic else f'subject.user == "{USER}"'
    listed = ",\n                   ".join(f'"{action}"' for action in ACTIONS)
    return (
        "// Written by the claude-voice power install script.\n"
        "polkit.addRule(function (action, subject) {\n"
        f"    var allowed = [{listed}];\n"
        f"    if (allowed.indexOf(action.id) >= 0 && {who}) {{\n"
        "        return polkit.Result.YES;\n"
        "    }\n"
        "});\n"
    )
