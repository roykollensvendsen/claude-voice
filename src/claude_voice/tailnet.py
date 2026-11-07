"""Tailscale, the private network between Roy's own machines: who is at an address on it."""

from __future__ import annotations

import ipaddress
import json
import subprocess

TAILNET = ipaddress.ip_network("100.64.0.0/10")


def on_tailnet(address: str) -> bool:
    """Whether `address` is a tailnet address (100.64.0.0/10)."""
    try:
        return ipaddress.ip_address(address) in TAILNET
    except ValueError:
        return False


def whois(address: str) -> tuple[str, str] | None:
    """(stable node id, machine name) of the tailnet node at `address`, or None."""
    if not on_tailnet(address):
        return None
    try:
        out = subprocess.run(
            ["tailscale", "whois", "--json", address], check=True, capture_output=True, text=True, timeout=5
        ).stdout
        node = json.loads(out)["Node"]
        return str(node["StableID"]), str(node.get("ComputedName") or node["Name"].split(".")[0])
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        return None
