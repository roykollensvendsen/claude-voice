"""A request counts as coming from this computer only when it arrives on the bridge's local port.

The bridge answers on two ports from one web server. The tailnet reaches the first one. The
second, the local port, is bound to 127.0.0.1 and never forwarded, and only a request that
arrived there may count as from here. The tailnet port never counts, whatever its headers say.
"""

import json
import socket
import threading
import time

import httpx
import pytest

from claude_voice import api, server


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def scope(server_address, host):
    return {
        "type": "http",
        "client": ("127.0.0.1", 50000),
        "server": server_address,
        "headers": [(b"host", host.encode())],
    }


def test_a_request_on_the_tailnet_port_is_not_from_here():
    assert api.from_here(scope(("127.0.0.1", 8811), "127.0.0.1:8811"), 8812) is False


def test_the_same_request_on_the_local_port_is_from_here():
    assert api.from_here(scope(("127.0.0.1", 8812), "127.0.0.1:8812"), 8812) is True


@pytest.mark.parametrize("address", [("0.0.0.0", 8812), ("::1", 8812), ("testserver", 80), None])
def test_only_the_local_port_on_127_0_0_1_counts_as_here(address):
    assert api.from_here(scope(address, "127.0.0.1:8812"), 8812) is False


# -- over real sockets: TestClient reports ("testserver", 80), so these run the real server ----


class Seen:
    """A small app: counts its starts, and says what each request looked like to the bridge."""

    def __init__(self, local):
        self.local, self.starts = local, 0

    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":
            while True:
                kind = (await receive())["type"]
                self.starts += kind == "lifespan.startup"
                await send({"type": f"{kind}.complete"})
                if kind == "lifespan.shutdown":
                    return
        from starlette.requests import Request

        body = {
            "here": api.from_here(scope, self.local),
            "client": scope["client"][0],
            "scheme": api._scheme(Request(scope)),
        }
        await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"application/json")]})
        await send({"type": "http.response.body", "body": json.dumps(body).encode()})


@pytest.fixture
def running():
    public, local = free_port(), free_port()
    app = Seen(local)
    web = server.web_server(app, "127.0.0.1", public, local)
    worker = threading.Thread(target=web.run, daemon=True)
    worker.start()
    for _ in range(100):
        if web.started:
            break
        time.sleep(0.05)
    yield app, web, public, local
    web.should_exit = True
    worker.join(server.SHUTDOWN_SECONDS + 5)


def ask(port, **headers):
    return httpx.get(f"http://127.0.0.1:{port}/", headers=headers, timeout=10).json()


def test_a_request_counts_as_from_here_only_on_the_local_socket(running):
    _, _, public, local = running
    assert ask(public, Host=f"127.0.0.1:{public}")["here"] is False
    assert ask(public, Host=f"127.0.0.1:{local}")["here"] is False
    assert ask(local)["here"] is True


def test_one_server_answers_both_ports_and_starts_once(running):
    app, _, public, local = running
    assert ask(public)["client"] == ask(local)["client"] == "127.0.0.1"
    assert app.starts == 1


def test_forwarding_headers_never_rewrite_who_asked_and_the_scheme_is_still_read(running):
    _, web, public, _ = running
    seen = ask(public, **{"X-Forwarded-For": "100.101.102.103", "X-Forwarded-Proto": "https"})
    assert web.config.proxy_headers is False
    assert seen["client"] == "127.0.0.1" and seen["scheme"] == "https"


def test_the_local_socket_is_bound_to_127_0_0_1_whatever_the_host():
    public, local = free_port(), free_port()
    web = server.web_server(Seen(local), "0.0.0.0", public, local)
    try:
        assert [s.getsockname()[:2] for s in web.sockets] == [("0.0.0.0", public), ("127.0.0.1", local)]
    finally:
        for s in web.sockets:
            s.close()


def test_a_local_port_already_in_use_stops_the_start_naming_both_ports():
    public = free_port()
    with socket.socket() as taken:
        taken.bind(("127.0.0.1", 0))
        taken.listen()
        local = taken.getsockname()[1]
        with pytest.raises(server.ConfigError, match=f"{local}.*{public}|{public}.*{local}"):
            server.web_server(Seen(local), "127.0.0.1", public, local)


def test_the_two_ports_can_not_be_the_same(tmp_path):
    env = {"CLAUDE_VOICE_ROOT": str(tmp_path), "CLAUDE_VOICE_PORT": "8811", "CLAUDE_VOICE_LOCAL_PORT": "8811"}
    with pytest.raises(server.ConfigError, match="CLAUDE_VOICE_LOCAL_PORT.*8811"):
        server.load_config(env, "stdio")


def test_the_local_port_is_8812_unless_set(tmp_path):
    assert server.load_config({"CLAUDE_VOICE_ROOT": str(tmp_path)}, "stdio").local_port == 8812
