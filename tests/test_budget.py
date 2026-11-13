"""Each client gets an answer within its own time budget, and slow work finishes in the background.

The Claude app drops a tool call that runs for about 30 seconds (kelno measured it), while
the voice app waits longer. So a message that takes Claude Code long to deliver is answered
"sending" in time, and the delivery is reported when it is done.
"""

import asyncio
import contextlib
import json
import os

import httpx
import pytest
from fakes import FakeClaude
from mcp.client import Client

from claude_voice import api, config
from claude_voice.oauth import OAuthProvider
from claude_voice.server import build_app, build_server
from claude_voice.sessions import SessionManager
from claude_voice.store import Store

PID = os.getpid()
SECRET = "s3cret-token-for-the-budget-tests-x"
HOST = "http://127.0.0.1:8811"


# -- the settings file -------------------------------------------------------------------


def test_the_claude_app_gets_ten_seconds_and_the_voice_app_two_minutes_by_default(tmp_path):
    # The app drops a call after about 30 seconds of its own clock, which may have run
    # for a while already when it reconnects, so the bridge answers well inside that.
    budgets = config.load(tmp_path / "none.toml", env={"HOME": str(tmp_path)}).budgets
    assert budgets.seconds("claude_app") == 10 and budgets.seconds("voice_app") == 120
    assert budgets.seconds("local") == 60  # anything else


def test_a_budget_is_set_per_client_in_the_settings_file(tmp_path):
    f = tmp_path / "c.toml"
    f.write_text("[budgets]\nclaude_app = 15\ndefault = 30\n")
    budgets = config.load(f, env={"HOME": str(tmp_path)}).budgets
    assert budgets.seconds("claude_app") == 15 and budgets.seconds("voice_app") == 120
    assert budgets.seconds("in_process") == 30


def test_a_time_budget_that_is_not_a_positive_number_is_refused(tmp_path):
    f = tmp_path / "c.toml"
    f.write_text('[budgets]\nclaude_app = "soon"\n')
    with pytest.raises(config.ConfigError, match="claude_app"):
        config.load(f, env={"HOME": str(tmp_path)})


# -- sending a message -------------------------------------------------------------------


class SlowCourier:
    """Stands in for Claude Code's SendMessage, which can take a minute or more."""

    def __init__(self, delivered=True):
        self.release, self.delivered, self.calls = asyncio.Event(), delivered, []

    async def __call__(self, name, text):
        self.calls.append((name, text))
        # Never wait for ever: with a rule switched off, a test must fail, not hang.
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self.release.wait(), 5)
        return {"delivered": self.delivered, "detail": "" if self.delivered else "the courier did not send it"}


@pytest.fixture
def world(tmp_path):
    (tmp_path / "home" / "billing").mkdir(parents=True)
    (tmp_path / "live").mkdir()
    (tmp_path / "live" / "billing-ab.json").write_text(
        json.dumps({"pid": PID, "sessionId": "s1", "cwd": str(tmp_path / "home" / "billing"), "name": "billing-ab"})
    )
    return tmp_path


def bridge(world, deliver, seconds=0.2):
    store = Store(world / "b.db")
    m = SessionManager(store, project_root=world / "home", client_factory=FakeClaude())
    repos = config.Config(
        config.Repos(),
        config.Paths(None, None, None, None),
        None,
        budgets=config.Budgets({"default": seconds}),
    )
    return build_server(
        m,
        oauth=OAuthProvider(store, SECRET, HOST, static_token=SECRET),
        conversations=lambda **k: [],
        live_dir=world / "live",
        projects_dir=world / "projects",
        deliver=deliver,
        repo_config=repos,
    )


async def test_a_message_sent_in_time_is_answered_as_delivered(world):
    courier = SlowCourier()
    courier.release.set()
    async with Client(bridge(world, courier)) as c:
        out = (
            await c.call_tool("message_active_session", {"session": "billing-ab", "message": "hi"})
        ).structured_content
    assert out["status"] == "delivered" and out["delivered"] is True


async def test_a_delivery_slower_than_the_clients_budget_is_answered_as_sending(world):
    courier = SlowCourier()
    server = bridge(world, courier)
    async with Client(server) as c:
        with api.HUB.subscribe() as events:
            out = (
                await asyncio.wait_for(
                    c.call_tool("message_active_session", {"session": "billing-ab", "message": "hello there"}), 2
                )
            ).structured_content
            rows = (await c.call_tool("fleet_recap", {})).structured_content["sessions"]
            courier.release.set()
            for _ in range(50):
                await asyncio.sleep(0.02)
                seen = []
                while not events.empty():
                    seen.append(events.get_nowait())
                done = [e for e in seen if e[0] == "message_sent"]
                if done:
                    break
            after = (await c.call_tool("fleet_recap", {})).structured_content["sessions"]
    assert out["status"] == "sending" and out["delivered"] is None and out["id"]
    sending = next(r for r in rows if r["name"] == "billing-ab")["sending"]
    assert [s["text"] for s in sending] == ["hello there"] and sending[0]["id"] == out["id"]
    assert done[0][1] == {"session": "s1", "id": out["id"], "delivered": True, "detail": ""}
    assert next(r for r in after if r["name"] == "billing-ab")["sending"] == []
    assert len(courier.calls) == 1  # sent once, not again


async def test_a_message_still_sending_says_it_is_not_yet_confirmed_and_what_to_do_next(world):
    courier = SlowCourier()
    async with Client(bridge(world, courier)) as c:
        out = (
            await c.call_tool("message_active_session", {"session": "billing-ab", "message": "hi"})
        ).structured_content
        courier.release.set()
    assert out["status"] == "sending"
    assert "on its way" in out["say"] and "not yet confirmed" in out["say"]
    assert "read_session_output" in out["say"]  # the step that shows whether it arrived


async def test_a_delivery_that_fails_in_the_background_is_reported_as_not_delivered(world):
    courier = SlowCourier(delivered=False)
    async with Client(bridge(world, courier)) as c:
        with api.HUB.subscribe() as events:
            await c.call_tool("message_active_session", {"session": "billing-ab", "message": "x"})
            courier.release.set()
            for _ in range(50):
                await asyncio.sleep(0.02)
                seen = [events.get_nowait() for _ in range(events.qsize())]
                done = [e for e in seen if e[0] == "message_sent"]
                if done:
                    break
    assert done[0][1]["delivered"] is False and "did not send" in done[0][1]["detail"]


# -- knowing the bridge restarted --------------------------------------------------------


async def test_the_app_can_tell_a_restart_from_a_dropped_connection(world):
    app = build_app(bridge(world, SlowCourier()))
    bearer = {"Authorization": f"Bearer {SECRET}"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=HOST) as c:
        started = (await c.get("/api/v1/bridge", headers=bearer)).json()["started_at"]
        stream = asyncio.create_task(c.get("/api/v1/events?max_events=1", headers=bearer))
        await asyncio.sleep(0.3)
        api.HUB.publish("ping", {})
        body = (await asyncio.wait_for(stream, 10)).text
    events = [line.removeprefix("event: ") for line in body.splitlines() if line.startswith("event: ")]
    first = next(line for line in body.splitlines() if line.startswith("data: "))
    assert started and events[0] == "hello"  # the first event on every connect
    assert json.loads(first.removeprefix("data: ")) == {"started_at": started}
