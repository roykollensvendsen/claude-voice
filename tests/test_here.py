"""ADR-CV-012: only a request from this computer itself, not one forwarded to it, may bring
a session's terminal to the front. Each part of the rule has its own test."""

import pytest
from test_api import BEARER, bridge, client  # noqa: F401  (fixtures)
from test_live import live, root  # noqa: F401  (fixtures)
from test_remote_api import MOD, FakeMachine

from claude_voice import api, terminals

PORT = 8811
OWN = f"http://127.0.0.1:{PORT}"


def scope(client="127.0.0.1", host=f"127.0.0.1:{PORT}", origin=None, extra=()):
    headers = [(b"host", host.encode())] if host is not None else []
    if origin is not None:
        headers.append((b"origin", origin.encode()))
    headers.extend((name.encode(), value.encode()) for name, value in extra)
    return {"type": "http", "client": (client, 50000), "server": ("127.0.0.1", PORT), "headers": headers}


@pytest.mark.parametrize("host", [f"127.0.0.1:{PORT}", f"localhost:{PORT}"])
@pytest.mark.parametrize("origin", [None, f"http://127.0.0.1:{PORT}", f"http://localhost:{PORT}"])
def test_a_request_from_this_computer_to_the_bridge_itself_is_from_here(host, origin):
    assert api.from_here(scope(host=host, origin=origin)) is True


def test_the_ipv6_loopback_is_this_computer_too():
    assert api.from_here(scope(client="::1")) is True


@pytest.mark.parametrize("peer", ["100.66.86.99", "192.168.1.20", "", "not-an-address"])
def test_a_request_from_another_computer_is_not_from_here(peer):
    assert api.from_here(scope(client=peer)) is False


@pytest.mark.parametrize(
    "header",
    [
        ("x-forwarded-for", "100.101.102.103"),
        ("forwarded", "for=100.101.102.103"),
        ("tailscale-user-login", "roy@example.com"),
        ("tailscale-user-name", "Roy"),
    ],
)
def test_a_forwarded_request_is_not_from_here(header):
    assert api.from_here(scope(extra=[header])) is False


@pytest.mark.parametrize(
    "host",
    [
        "precision.example.ts.net:8814",
        "100.66.86.99:8813",
        f"127.0.0.1:{PORT + 1}",
        "127.0.0.1",
        f"evil.example:{PORT}",
        None,
    ],
)
def test_a_request_to_another_address_is_not_from_here(host):
    assert api.from_here(scope(host=host)) is False


@pytest.mark.parametrize(
    "origin", ["https://evil.example", "https://ui.example", f"http://127.0.0.1:{PORT + 1}", "null", ""]
)
def test_a_request_sent_by_another_website_is_not_from_here(origin):
    assert api.from_here(scope(origin=origin)) is False


# -- the endpoints -----------------------------------------------------------------------


class FakeTerminals:
    """Stands in for the desktop: remembers which session it was asked to bring up."""

    def __init__(self, answer=terminals.RAISED):
        self.answer, self.opened = answer, []

    def open(self, session_id, running, pane_of):
        self.opened.append(session_id)
        return self.answer


@pytest.fixture
def desk(bridge):  # noqa: F811
    server, app, _, _ = bridge
    fake = FakeTerminals()
    server.terminals = fake
    return server, app, fake


async def test_whoami_says_here_to_this_computer_and_not_to_a_forwarded_request(bridge):  # noqa: F811
    _, app, _, _ = bridge
    async with client(app) as c:
        here = await c.get("/api/v1/whoami")
        forwarded = await c.get("/api/v1/whoami", headers={"X-Forwarded-For": "100.101.102.103"})
    assert here.status_code == 200 and here.json() == {"here": True}
    assert forwarded.status_code == 200 and forwarded.json() == {"here": False}
    assert here.headers["cache-control"] == "no-store"


async def test_a_session_is_brought_to_the_front_for_a_request_from_here(desk):
    _, app, fake = desk
    async with client(app) as c:
        out = await c.post("/api/v1/sessions/current-id/focus", headers={**BEARER, "Origin": OWN})
    assert out.status_code == 200 and out.json() == {"done": "raised"}
    assert fake.opened == ["current-id"]


async def test_only_a_request_from_here_may_bring_a_terminal_to_the_front(desk):
    _, app, fake = desk
    async with client(app) as c:
        out = await c.post(
            "/api/v1/sessions/current-id/focus", headers={**BEARER, "X-Forwarded-For": "100.101.102.103"}
        )
    assert out.status_code == 403 and out.json() == {"reason": "not_here"}
    assert fake.opened == []


async def test_focus_sent_by_another_website_is_refused(desk):
    _, app, fake = desk
    async with client(app) as c:
        out = await c.post("/api/v1/sessions/current-id/focus", headers={**BEARER, "Origin": "https://evil.example"})
    assert out.status_code == 403 and out.json() == {"reason": "not_here"}
    assert fake.opened == []


async def test_focus_needs_signing_in_even_from_here(desk):
    _, app, fake = desk
    async with client(app) as c:
        out = await c.post("/api/v1/sessions/current-id/focus")
    assert out.status_code == 401 and fake.opened == []


async def test_a_session_on_another_machine_is_not_local(desk):
    server, app, fake = desk
    server.fleet.machines = [FakeMachine(sessions=[MOD])]
    await server.fleet.refresh()
    async with client(app) as c:
        out = await c.post("/api/v1/sessions/lukas-laptop/840ab631/focus", headers=BEARER)
    assert out.status_code == 409 and out.json() == {"reason": "not_local"}
    assert fake.opened == []


@pytest.mark.parametrize(
    "answer", [terminals.ATTACHED, terminals.NO_WINDOW, terminals.NOT_RUNNING, terminals.NO_WINDOW_LIST]
)
async def test_the_desktop_s_answer_is_passed_on_as_its_code(desk, answer):
    _, app, fake = desk
    fake.answer = answer
    async with client(app) as c:
        out = await c.post("/api/v1/sessions/current-id/focus", headers=BEARER)
    assert (out.status_code, out.json()) == answer


async def test_the_bridge_s_own_desktop_finds_a_running_session_by_its_id(bridge):  # noqa: F811
    server, app, _, _ = bridge
    raised = []

    class Windows:
        def list(self):
            return [{"window_id": 4, "title": "✳ billing-ab", "wm_class": "org.gnome.Terminal"}]

        def activate(self, window_id):
            raised.append(window_id)
            return True

    server.terminals = terminals.Terminals(Windows(), run=lambda c: "", spawn=lambda c: None)
    async with client(app) as c:
        gone = await c.post("/api/v1/sessions/no-such-id/focus", headers=BEARER)
        found = await c.post("/api/v1/sessions/current-id/focus", headers=BEARER)
    assert (gone.status_code, gone.json()) == (409, {"reason": "not_running"})
    # the fixture's session has no "kind": not known to be a terminal, so nothing is raised
    assert (found.status_code, found.json()) == (409, {"reason": "no_window"}) and raised == []
