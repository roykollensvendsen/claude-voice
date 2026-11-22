"""The approver's own token at the bridge's doors: the login knows it as the approver, and
every door but /mcp refuses it (approver-v1 v1.7)."""

from __future__ import annotations

import subprocess
import sys

import httpx
import pytest
from fakes import FakeClaude

from claude_voice import approver_token, boxed
from claude_voice.oauth import OAuthProvider
from claude_voice.server import build_app, build_server
from claude_voice.sessions import SessionManager
from claude_voice.store import Store

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="the approver needs /proc")

SECRET = "s3cret-token-for-the-login-tests-xx"
BASE = "http://127.0.0.1:8811"


@pytest.fixture
def pane():
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    yield child.pid
    child.kill()
    child.wait()


def _bridge(tmp_path):
    store = Store(tmp_path / "b.db")
    oauth = OAuthProvider(store, SECRET, BASE, static_token=SECRET)
    server = build_server(
        SessionManager(store, project_root=tmp_path, client_factory=FakeClaude()),
        oauth=oauth,
        conversations=lambda **k: [],
        live_dir=tmp_path,
    )
    return server, oauth


def _approver_token(server, pid: int) -> str:
    keeper = server.approver_token  # type: ignore[attr-defined]
    keeper.mint(pid)
    return keeper.hand(pid)


async def test_the_login_knows_the_approvers_token_as_the_approver(tmp_path, pane):
    server, oauth = _bridge(tmp_path)
    token = _approver_token(server, pane)
    found = await oauth.load_access_token(token)
    assert found is not None and found.client_id == boxed.CLIENT
    assert boxed.gate in server.middleware


async def test_the_rest_api_refuses_the_approvers_token(tmp_path, pane):
    server, _ = _bridge(tmp_path)
    token = _approver_token(server, pane)
    app = build_app(server)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE) as c:
        mine = await c.get("/api/v1/roles", headers={"Authorization": f"Bearer {token}"})
        owners = await c.get("/api/v1/roles", headers={"Authorization": f"Bearer {SECRET}"})
    assert mine.status_code == 401
    assert owners.status_code == 200


async def test_a_dead_approvers_token_opens_no_door(tmp_path, pane, monkeypatch):
    server, oauth = _bridge(tmp_path)
    token = _approver_token(server, pane)
    monkeypatch.setattr(approver_token.lineage, "start_ticks", lambda pid: None)  # the pane is gone
    assert await oauth.load_access_token(token) is None
