"""The local port must never be forwarded: `check` fails when it is, and the bridge says so at start.

`tailscale serve status --json` lists what the tailnet reaches: TCP forwards, web handlers, and
which of those Funnel opens to the internet. Any of them leading to the local port undoes what
the port is for, however its target is written.
"""

import logging

import pytest

from claude_voice import cli, doctor, server

LOCAL = 8812


def tcp(target, listen="8813"):
    return {"TCP": {listen: {"TCPForward": target}}}


def web(target, hostport="box.example.ts.net:8814", funnel=False):
    status = {"Web": {hostport: {"Handlers": {"/": {"Proxy": target}}}}}
    if funnel:
        status["AllowFunnel"] = {hostport: True}
    return status


@pytest.mark.parametrize(
    "status",
    [
        tcp("127.0.0.1:8812"),
        tcp("localhost:8812"),
        tcp("[::1]:8812"),
        tcp("tcp://127.0.0.1:8812"),
        web("http://127.0.0.1:8812"),
        web("https://localhost:8812"),
        web("https+insecure://[::1]:8812/"),
        web("http://127.0.0.1:8812", hostport="box.example.ts.net:443", funnel=True),
    ],
)
def test_any_forward_of_the_local_port_fails_the_check(status):
    (found,) = doctor.local_port_forwards(status, LOCAL)
    assert "off" in found["fix"] and str(LOCAL) in found["detail"]


@pytest.mark.parametrize(
    "status",
    [
        {},
        tcp("127.0.0.1:8811"),
        web("http://127.0.0.1:8811"),
        web("http://127.0.0.1:8811", hostport="box.example.ts.net:443", funnel=True),
        web("http://127.0.0.1:18812"),
        web("http://127.0.0.1:5173"),  # a dev preview on its own port, proxying to 8811 itself
    ],
)
def test_forwards_of_other_ports_are_left_alone(status):
    assert doctor.local_port_forwards(status, LOCAL) == []


def test_the_fix_names_the_forward_to_switch_off():
    (on_tcp,) = doctor.local_port_forwards(tcp("127.0.0.1:8812", listen="8813"), LOCAL)
    (on_web,) = doctor.local_port_forwards(web("http://127.0.0.1:8812"), LOCAL)
    (on_funnel,) = doctor.local_port_forwards(web("http://127.0.0.1:8812", "box.example.ts.net:443", True), LOCAL)
    assert "tailscale serve --tcp=8813 off" in on_tcp["fix"]
    assert "tailscale serve --https=8814 off" in on_web["fix"]
    assert "tailscale funnel --https=443 off" in on_funnel["fix"]


def test_check_fails_while_the_local_port_is_forwarded(tmp_path):
    env = {"CLAUDE_VOICE_ROOT": str(tmp_path)}
    _, found = cli.checks(env, "stdio", lambda: (True, "signed in"), serve_status=lambda: tcp("127.0.0.1:8812"))
    (port,) = [c for c in found if c["name"] == "local_port"]
    assert port["ok"] is False and port["required"] is True
    assert "tailscale serve --tcp=8813 off" in port["fix"]


def test_check_passes_when_the_local_port_is_not_forwarded(tmp_path):
    env = {"CLAUDE_VOICE_ROOT": str(tmp_path)}
    _, found = cli.checks(env, "stdio", lambda: (True, "signed in"), serve_status=lambda: tcp("127.0.0.1:8811"))
    (port,) = [c for c in found if c["name"] == "local_port"]
    assert port["ok"] is True


def test_the_bridge_says_at_start_when_the_local_port_is_forwarded(caplog):
    with caplog.at_level(logging.ERROR, logger="claude_voice"):
        server.warn_forwarded_local_port(LOCAL, lambda: tcp("127.0.0.1:8812"))
    assert "tailscale serve --tcp=8813 off" in caplog.text
