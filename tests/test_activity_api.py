"""The activity view's endpoints: the switch, clearing, the series and what runs now."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from starlette.applications import Starlette
from starlette.routing import Route
from test_activity import T0, clock, machine, make, on, prompt, result, use, write  # noqa: F401  (fixtures)

from claude_voice import api
from claude_voice.store import Store

SECRET = "a-secret-for-the-activity-tests"
HOST = "http://127.0.0.1:8811"
BEARER = {"Authorization": f"Bearer {SECRET}"}
PHONE = {**BEARER, "Tailscale-User-Login": "someone@example.test"}


class Routes:
    def __init__(self) -> None:
        self.routes: list[Route] = []

    def custom_route(self, path: str, methods: list[str]) -> Any:
        def add(fn: Any) -> Any:
            self.routes.append(Route(path, fn, methods=methods))
            return fn

        return add


@pytest.fixture
def web(tmp_path, machine, clock):  # noqa: F811
    store = Store(tmp_path / "web.db")
    act = make(tmp_path, machine, clock)

    async def check(token: str) -> bool:
        return token == SECRET

    events = api.EventApi(
        store,
        SECRET,
        HOST,
        check,
        sessions=lambda: {},
        get_active=lambda: None,
        set_active=lambda a, b: {},
        output=lambda *a: {},
        pending=lambda s: {},
        prs=None,  # type: ignore[arg-type]
        news=lambda c: {},
        activity=act,
    )
    routes = Routes()
    events.routes(routes)
    app = Starlette(routes=routes.routes)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=HOST), act


async def test_the_activity_api_needs_a_sign_in(web):
    client, _ = web
    async with client:
        for method, path in (
            ("GET", "/api/v1/activity"),
            ("POST", "/api/v1/activity"),
            ("POST", "/api/v1/activity/clear"),
            ("GET", "/api/v1/activity/series?scope=all&window=1h"),
            ("GET", "/api/v1/activity/now?scope=all"),
        ):
            r = await client.request(method, path)
            assert r.status_code == 401, path
            assert set(r.json()) == {"error"}


async def test_the_activity_api_switches_and_serves(web, machine):  # noqa: F811
    client, _ = web
    path = machine.session("worker", "voice")
    write(path, prompt(T0), use(T0 + 1, "c1"))
    async with client:
        off = (await client.get("/api/v1/activity", headers=BEARER)).json()
        assert off["on"] is False and off["until"] is None
        assert (await client.get("/api/v1/activity/now?scope=all", headers=BEARER)).status_code == 404
        r = await client.post("/api/v1/activity", headers=BEARER, json={"on": True, "for": "1h"})
        assert r.status_code == 200 and r.json()["on"] is True
        assert set(r.json()) == {"on", "until", "durations", "scopes"}
        series = await client.get("/api/v1/activity/series?scope=all&window=1h", headers=BEARER)
        assert set(series.json()) == {"scope", "window", "bucket_s", "from", "to", "recording", "points"}
        now = await client.get("/api/v1/activity/now?scope=all", headers=BEARER)
        assert set(now.json()) == {"at", "sessions"}
        bad = await client.post("/api/v1/activity", headers=BEARER, json={"on": True, "for": "2h"})
        assert bad.status_code == 400
        assert (await client.get("/api/v1/activity/series?scope=all&window=2h", headers=BEARER)).status_code == 400
        assert (await client.get("/api/v1/activity/series?scope=org:x&window=1h", headers=BEARER)).status_code == 404
        cleared = await client.post("/api/v1/activity/clear", headers=BEARER)
        assert cleared.json() == {"cleared": True}


async def test_the_phone_gets_no_guarded_rows(web, machine, clock):  # noqa: F811
    client, act = web
    path = machine.session("kept", "guarded")
    on(act)
    write(path, use(T0 + 1, "c1"), result(T0 + 2, "c1"))
    clock.go(10)
    act.tick()
    async with client:
        r = await client.get("/api/v1/activity/series?scope=org:guarded&window=1h", headers=PHONE)
        assert r.status_code == 404
        r = await client.get("/api/v1/activity/series?scope=org:guarded&window=1h", headers=BEARER)
        assert r.status_code == 200 and r.json()["points"]


async def test_a_switch_from_another_site_is_refused(web):
    client, _ = web
    async with client:
        await client.post("/api/v1/signin", json={"secret": SECRET})
        r = await client.post("/api/v1/activity", json={"on": True}, headers={"Origin": "https://elsewhere.example"})
        assert r.status_code == 403
        assert set(r.json()) == {"error"}
