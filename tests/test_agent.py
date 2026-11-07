"""The agent: claude-voice on another machine, answering only the hub it was paired with.

The hub here is a stand-in: an HTTP client that says which tailnet address it calls from.
"""

import json

import httpx
import pytest
from mcp.client import Client
from mcp.client.streamable_http import streamable_http_client

from claude_voice import agent
from claude_voice.config import PluginSpec

HUB = "100.66.86.99"
HUB_NODE = "nHUBstable"
OTHER = "100.79.48.77"  # the phone: on the tailnet, never paired
NODES = {HUB: (HUB_NODE, "precision7530-arch-roy"), OTHER: ("nPHONEstable", "roy-sin-xcover7-pro")}
HERE = "100.86.231.118"


def whois(address):
    return NODES.get(address)


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def pairing(tmp_path, clock):
    return agent.Pairing(tmp_path / "agent", clock=clock)


def app(pairing, specs=(), approvals=None, client=None):
    server = agent.build_agent(
        "hjemme", pairing, specs=list(specs), plugin_approvals=approvals, plugin_client=client, address=HERE
    )
    return agent.build_agent_app(server, pairing, whois=whois, address=HERE)


def http(application, caller=HUB, key=None):
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    transport = httpx.ASGITransport(app=application, client=(caller, 4321))
    return httpx.AsyncClient(transport=transport, base_url=f"http://{HERE}:{agent.PORT}", headers=headers)


async def pair(application, code, caller=HUB):
    async with http(application, caller) as h:
        return await h.post("/pair", json={"code": code})


async def paired(pairing, application):
    r = await pair(application, pairing.start())
    assert r.status_code == 200, r.text
    return r.json()["key"]


async def tools(application, key, caller=HUB):
    async with (
        application.server.session_manager.run(),
        http(application, caller, key) as h,
        Client(streamable_http_client(f"http://{HERE}:{agent.PORT}/mcp", http_client=h)) as c,
    ):
        listed = [t.name for t in (await c.list_tools()).tools]
        info = (await c.call_tool("agent_info", {})).structured_content
    return listed, info


# -- where it listens -----------------------------------------------------------


def test_the_agent_listens_on_its_tailnet_address_only():
    assert agent.listen_address(lambda: "100.86.231.118\n") == "100.86.231.118"
    for answer in ("", "0.0.0.0", "192.168.10.198", "not an address"):
        with pytest.raises(agent.AgentError, match="tailscale up"):
            agent.listen_address(lambda answer=answer: answer)


def test_the_agent_does_not_start_without_tailscale():
    def missing():
        raise FileNotFoundError("tailscale")

    with pytest.raises(agent.AgentError, match="Tailscale"):
        agent.listen_address(missing)


# -- pairing --------------------------------------------------------------------


def test_a_pairing_code_is_eight_characters_easy_to_read_aloud(pairing):
    code = pairing.start()
    assert len(code) == 8 and set(code) <= set(agent.CODE_LETTERS)
    assert not set("0O1lI") & set(agent.CODE_LETTERS)


async def test_the_paired_hub_gets_a_key_that_reaches_the_agent(pairing):
    application = app(pairing)
    key = await paired(pairing, application)
    listed, info = await tools(application, key)
    assert "agent_info" in listed
    assert info["machine"] == "hjemme" and info["hub"] == "precision7530-arch-roy"


async def test_a_pairing_code_works_once_for_ten_minutes(pairing, clock):
    application = app(pairing)
    code = pairing.start()
    assert (await pair(application, code)).status_code == 200
    assert (await pair(application, code)).status_code == 403
    code = pairing.start()
    clock.now += 10 * 60 + 1
    r = await pair(application, code)
    assert r.status_code == 403 and "agent pair" in r.json()["error"]


async def test_five_wrong_pairing_codes_end_the_pairing(pairing):
    application = app(pairing)
    code = pairing.start()
    for _ in range(5):
        assert (await pair(application, "WRONGCOD")).status_code == 403
    assert (await pair(application, code)).status_code == 403


async def test_pairing_from_outside_the_tailnet_is_refused(pairing):
    application = app(pairing)
    r = await pair(application, pairing.start(), caller="192.168.10.50")
    assert r.status_code == 403


async def test_the_key_is_stored_only_as_a_hash(pairing, tmp_path):
    application = app(pairing)
    key = await paired(pairing, application)
    for f in (tmp_path / "agent").iterdir():
        assert key not in f.read_text()
        assert f.stat().st_mode & 0o077 == 0


async def test_pairing_again_replaces_the_old_hub(pairing):
    application = app(pairing)
    old = await paired(pairing, application)
    new = await paired(pairing, application)
    async with http(application, key=old) as h:
        assert (await h.post("/mcp", json={})).status_code == 401
    assert (await tools(application, new))[1]["machine"] == "hjemme"


# -- every call -----------------------------------------------------------------


@pytest.mark.parametrize("auth", [None, "Bearer wrong", "Basic x"])
async def test_a_call_needs_the_key_and_the_tailnet_identity_of_the_paired_hub(pairing, auth):
    application = app(pairing)
    await paired(pairing, application)
    headers = {"Authorization": auth} if auth else {}
    async with http(application) as h:
        r = await h.post("/mcp", headers=headers, json={})
    assert r.status_code == 401


async def test_the_key_from_another_machine_is_refused(pairing):
    application = app(pairing)
    key = await paired(pairing, application)
    async with http(application, caller=OTHER, key=key) as h:
        assert (await h.post("/mcp", json={})).status_code == 401
    async with http(application, caller="192.168.10.50", key=key) as h:
        assert (await h.post("/mcp", json={})).status_code == 401


async def test_an_unpaired_agent_answers_nobody(pairing):
    application = app(pairing)
    async with http(application, key="anything") as h:
        assert (await h.post("/mcp", json={})).status_code == 401


# -- what it offers -------------------------------------------------------------


class FakePlugin:
    """A plugin's MCP client: one tool that answers, one the owner never allowed."""

    def __init__(self, params):
        self.params = params

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    instructions = None

    async def list_tools(self):
        from mcp.types import ListToolsResult, Tool

        schema = {"type": "object", "properties": {}}
        return ListToolsResult(
            tools=[
                Tool(name="status", description="the door", input_schema=schema),
                Tool(name="press", description="never allowed", input_schema=schema),
            ]
        )

    async def call_tool(self, name, arguments):
        from mcp.types import CallToolResult, TextContent

        return CallToolResult(content=[TextContent(type="text", text=json.dumps({"door": "closed"}))])


def door(tmp_path):
    return PluginSpec(name="door", command="door-mcp", args=[], cwd=str(tmp_path), tools=["status"])


async def test_the_agent_offers_only_the_plugins_its_owner_approved(pairing, tmp_path):
    from claude_voice import plugins

    approvals = tmp_path / "approved.json"
    spec = door(tmp_path)
    unapproved = PluginSpec(name="lock", command="lock-mcp", args=[], cwd=str(tmp_path), tools=["status"])
    plugins.approve(spec, {"status": {"schema": {"type": "object", "properties": {}}}}, approvals)
    application = app(pairing, [spec, unapproved], approvals, FakePlugin)
    key = await paired(pairing, application)
    listed, info = await tools(application, key)
    assert sorted(listed) == ["agent_info", "door_status"]
    assert info["tools"] == ["door_status"]


def test_agent_pair_shows_a_code_and_what_to_run_on_the_hub(tmp_path, capsys):
    from claude_voice.cli import main

    assert main(["agent", "pair"], env={"HOME": str(tmp_path)}) == 0
    out = capsys.readouterr().out
    code = out.split("Pairing code: ")[1].split()[0]
    assert len(code) == 8 and "claude-voice machines approve" in out
    assert (tmp_path / ".config/claude-voice/agent/pairing.json").stat().st_mode & 0o077 == 0
