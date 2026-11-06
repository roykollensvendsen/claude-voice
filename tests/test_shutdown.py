"""Stopping the bridge is bounded: an open stream or a slow child never holds a restart up.

On 2026-11-06 a restart hung for 90 seconds, until systemd killed the bridge: uvicorn
waited for the app's event stream and the voice's MCP streams to close, and they never do.
The voice heard "server isn't responding" meanwhile.
"""

import asyncio
import configparser
import socket
import threading
import time
from pathlib import Path

import httpx

from claude_voice import server

UNIT = Path(__file__).parent.parent / "deploy" / "claude-voice.service"


async def endless(scope, receive, send):
    """An app whose one response never ends, like the event stream."""
    if scope["type"] == "lifespan":
        while (await receive())["type"] != "lifespan.shutdown":
            pass
        return
    await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"text/event-stream")]})
    while True:
        await send({"type": "http.response.body", "body": b": ping\n\n", "more_body": True})
        await asyncio.sleep(0.2)


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_the_bridge_stops_within_seconds_even_with_a_stream_open():
    port = free_port()
    web = server.web_server(endless, "127.0.0.1", port)
    worker = threading.Thread(target=web.run, daemon=True)
    worker.start()
    for _ in range(100):
        if web.started:
            break
        time.sleep(0.05)
    opened = threading.Event()

    def listen():
        try:
            with httpx.stream("GET", f"http://127.0.0.1:{port}/", timeout=30) as r:
                for _ in r.iter_bytes():
                    opened.set()
        except httpx.HTTPError:
            pass

    threading.Thread(target=listen, daemon=True).start()
    assert opened.wait(5)
    asked = time.monotonic()
    web.should_exit = True  # what SIGTERM sets
    worker.join(server.SHUTDOWN_SECONDS + 5)
    assert not worker.is_alive() and time.monotonic() - asked < server.SHUTDOWN_SECONDS + 3


def test_systemd_kills_what_is_left_soon_after_asking_it_to_stop():
    unit = configparser.ConfigParser(strict=False)
    unit.read(UNIT)
    service = unit["Service"]
    assert int(service["TimeoutStopSec"]) <= 20  # not systemd's 90 seconds
    assert int(service["TimeoutStopSec"]) > server.SHUTDOWN_SECONDS  # the bridge's own wait comes first
    assert service.get("KillMode", "control-group") in ("control-group", "mixed")  # plugin children go too
