"""The hub's side of another machine: pairing with its agent, and offering what Roy approved.

The other machine here is a real agent app, reached in memory; each side has its own
stand-in for what Tailscale says about the other.
"""

import asyncio
import time

import httpx
import pytest
from mcp.client import Client
from mcp.server import MCPServer

from claude_voice import agent, machines
from claude_voice.config import ConfigError, MachineSpec, PluginSpec, load

HUB = "100.66.86.99"
LAPTOP = "100.86.231.118"
NODES = {HUB: ("nHUB", "precision7530-arch-roy"), LAPTOP: ("nLAPTOP", "hjemme")}
SPEC = MachineSpec(name="lukas-laptop", address=LAPTOP)


class Laptop:
    """Lukas's laptop: its agent, and what the hub's Tailscale says is at its address."""

    def __init__(self, tmp_path, specs=(), approvals=None, client=None, bridge=None):
        self.pairing = agent.Pairing(tmp_path / "agent")
        server = agent.build_agent(
            "hjemme",
            self.pairing,
            specs=list(specs),
            plugin_approvals=approvals,
            plugin_client=client,
            address=LAPTOP,
            bridge=bridge,
        )
        self.app = agent.build_agent_app(server, self.pairing, whois=NODES.get, address=LAPTOP)
        self.seen_as = dict(NODES)

    def http(self, spec, headers):
        transport = httpx.ASGITransport(app=self.app, client=(HUB, 4321))
        return httpx.AsyncClient(transport=transport, base_url=machines.url(spec), headers=headers)

    def whois(self, address):
        return self.seen_as.get(address)

    async def __aenter__(self):
        self._running = self.app.server.session_manager.run()
        await self._running.__aenter__()
        return self

    async def __aexit__(self, *exc):
        return await self._running.__aexit__(*exc)


@pytest.fixture
def laptop(tmp_path):
    return Laptop(tmp_path)


def typed(*answers):
    """Roy at the keyboard: each prompt gets the next answer."""
    queue = list(answers)
    return lambda prompt: queue.pop(0)


async def approve(laptop, folder, name="lukas-laptop", code=None):
    lines = []
    out = await machines.pair(
        SPEC,
        ask=typed(code or laptop.pairing.start(), name),
        say=lines.append,
        http=laptop.http,
        whois=laptop.whois,
        folder=folder,
    )
    return out, "\n".join(lines)


def hub(laptop, folder):
    server = MCPServer("hub")
    started = machines.start_approved(server, [SPEC], folder, http=laptop.http, whois=laptop.whois)
    return server, started


async def call(server, tool, **args):
    async with Client(server) as c:
        return await c.call_tool(tool, args)


# -- pairing --------------------------------------------------------------------


async def test_a_machine_is_approved_by_typing_its_pairing_code_and_then_its_name(laptop, tmp_path):
    async with laptop:
        folder = tmp_path / "hub"
        key = folder / "machines" / "lukas-laptop" / "key"
        out, _ = await approve(laptop, folder, name="yes")
        assert not out["approved"] and not key.exists()
        out, shown = await approve(laptop, folder)
        assert out["approved"] and "hjemme" in shown and "agent_info" in shown
        assert key.stat().st_mode & 0o077 == 0
        server, started = hub(laptop, folder)
        assert [m.offered for m in started] == [["lukas-laptop_agent_info"]]
        res = await call(server, "lukas-laptop_agent_info")
        assert not res.is_error and res.structured_content["machine"] == "hjemme"


async def test_typing_another_name_approves_nothing(laptop, tmp_path):
    async with laptop:
        folder = tmp_path / "hub"
        out, shown = await approve(laptop, folder, name="yes")
        assert not out["approved"] and "Not approved" in shown
        assert not (folder / "machines" / "lukas-laptop" / "key").exists()
        assert hub(laptop, folder)[1] == []


async def test_a_wrong_pairing_code_approves_nothing(laptop, tmp_path):
    async with laptop:
        laptop.pairing.start()
        out, shown = await approve(laptop, tmp_path / "hub", code="WRONGCOD")
        assert not out["approved"] and "agent pair" in shown


async def test_pairing_with_an_address_off_the_tailnet_is_refused(laptop, tmp_path):
    async with laptop:
        laptop.seen_as.pop(LAPTOP)
        out, shown = await approve(laptop, tmp_path / "hub")
        assert not out["approved"] and "tailnet" in shown


# -- calling --------------------------------------------------------------------


async def test_the_hub_talks_only_to_the_machine_tailscale_says_it_approved(laptop, tmp_path):
    async with laptop:
        folder = tmp_path / "hub"
        await approve(laptop, folder)
        server, _ = hub(laptop, folder)
        laptop.seen_as[LAPTOP] = ("nSTRANGER", "hjemme")  # another node took the address
        res = await call(server, "lukas-laptop_agent_info")
        assert res.is_error and "not the machine" in res.content[0].text


async def test_a_machine_whose_tools_changed_is_not_called_until_approved_again(tmp_path):
    from test_agent import FakePlugin

    from claude_voice import plugins

    folder = tmp_path / "hub"
    async with Laptop(tmp_path / "before") as before:
        await approve(before, folder)
    door = PluginSpec(name="door", command="door-mcp", args=[], cwd=str(tmp_path), tools=["status"])
    own = tmp_path / "laptop-approved.json"
    plugins.approve(door, {"status": {"schema": {"type": "object", "properties": {}}}}, own)
    async with Laptop(tmp_path / "before", [door], own, FakePlugin) as after:  # same pairing, one tool more
        server, _ = hub(after, folder)
        res = await call(server, "lukas-laptop_agent_info")
    assert res.is_error and "machines approve lukas-laptop" in res.content[0].text


async def test_a_machine_that_does_not_answer_in_two_seconds_is_reported_not_waited_for(laptop, tmp_path):
    async with laptop:
        folder = tmp_path / "hub"
        await approve(laptop, folder)

        async def silent(request):
            await asyncio.sleep(30)

        def asleep(spec, headers):
            return httpx.AsyncClient(
                transport=httpx.MockTransport(silent), base_url=machines.url(spec), headers=headers
            )

        server = MCPServer("hub")
        machines.start_approved(server, [SPEC], folder, http=asleep, whois=laptop.whois)
        started = time.monotonic()
        res = await call(server, "lukas-laptop_agent_info")
        assert time.monotonic() - started < 4
        assert res.is_error and "lukas-laptop did not answer" in res.content[0].text


# -- settings -------------------------------------------------------------------


def test_a_machine_is_named_in_the_settings_with_its_tailnet_address(tmp_path):
    f = tmp_path / "settings.toml"
    f.write_text('[machines.lukas-laptop]\naddress = "100.86.231.118"\n')
    assert load(f, env={"HOME": str(tmp_path)}).machines == [SPEC]


@pytest.mark.parametrize(
    "body", ['address = "192.168.10.198"', 'address = "hjemme"', "", 'address = "100.86.231.118"\nport = "x"']
)
def test_a_machine_without_a_tailnet_address_is_refused(tmp_path, body):
    f = tmp_path / "settings.toml"
    f.write_text(f"[machines.lukas-laptop]\n{body}\n")
    with pytest.raises(ConfigError, match="tailnet"):
        load(f, env={"HOME": str(tmp_path)})


async def test_the_bridge_offers_an_approved_machines_tools(laptop, tmp_path):
    from test_plugins import bridge_with

    async with laptop:
        folder = tmp_path / "hub"
        await approve(laptop, folder)
        server = bridge_with(tmp_path, machines=[SPEC], machine_folder=folder, http=laptop.http, whois=laptop.whois)
        res = await call(server, "lukas-laptop_agent_info")
    assert not res.is_error and res.structured_content["machine"] == "hjemme"


async def test_machines_list_says_which_are_approved(laptop, tmp_path, capsys):
    from claude_voice.cli import main

    home = tmp_path / "home"
    (home / ".config/claude-voice").mkdir(parents=True)
    settings = home / "settings.toml"
    settings.write_text('[machines.lukas-laptop]\naddress = "100.86.231.118"\n')
    env = {"HOME": str(home), "CLAUDE_VOICE_CONFIG": str(settings)}
    assert main(["machines"], env=env) == 0
    assert "not approved; run: claude-voice machines approve lukas-laptop" in capsys.readouterr().out
    async with laptop:
        await approve(laptop, home / ".config/claude-voice")
    assert main(["machines"], env=env) == 0
    assert "approved, as hjemme; tools lukas-laptop_agent_info" in capsys.readouterr().out
