import asyncio
import base64
import contextlib
import hashlib
import secrets
import socket
import time
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import uvicorn
from fakes import FakeClaude

from claude_voice.oauth import OAuthProvider
from claude_voice.server import build_app, build_server
from claude_voice.sessions import SessionManager
from claude_voice.store import Store

SECRET = "the-owners-login-secret-0123456789abcdef"
REDIRECT = "https://chatgpt.example/connector/oauth/callback"


class Clock:
    def __init__(self) -> None:
        # The SDK also checks expiry against the wall clock, so start from it.
        self.t = time.time()

    def __call__(self) -> float:
        return self.t


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def root(tmp_path):
    (tmp_path / "src" / "app").mkdir(parents=True)
    return tmp_path / "src"


@contextlib.asynccontextmanager
async def served(tmp_path, root, clock, **oauth_options):
    """The bridge over HTTP on a free port, with its own sign-in; yields a client for it."""
    port = free_port()
    url = f"http://127.0.0.1:{port}"
    store = Store(tmp_path / "bridge.db", clock=clock)
    oauth = OAuthProvider(store, login_secret=SECRET, public_url=url, clock=clock, **oauth_options)
    manager = SessionManager(store, project_root=root, client_factory=FakeClaude())
    app = build_app(build_server(manager, oauth=oauth))
    uv = uvicorn.Server(uvicorn.Config(app, port=port, log_level="warning"))
    task = asyncio.create_task(uv.serve())
    while not uv.started:  # noqa: ASYNC110 - uvicorn exposes no event
        await asyncio.sleep(0.01)
    async with httpx.AsyncClient(base_url=url) as http:
        yield http
    uv.should_exit = True
    await task


@pytest.fixture
async def bridge(tmp_path, root, clock):
    async with served(tmp_path, root, clock) as http:
        yield http


def pkce():
    verifier = secrets.token_urlsafe(48)
    digest = hashlib.sha256(verifier.encode()).digest()
    return verifier, base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def registration(name="ChatGPT"):
    return {
        "client_name": name,
        "redirect_uris": [REDIRECT],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
    }


async def register(http, name="ChatGPT"):
    r = await http.post("/register", json=registration(name))
    assert r.status_code == 201, r.text
    return r.json()["client_id"]


def authorize_params(client_id, challenge, state="st-1"):
    return {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": REDIRECT,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
    }


async def start_authorize(http, client_id, challenge, state="st-1"):
    r = await http.get("/authorize", params=authorize_params(client_id, challenge, state))
    assert r.status_code == 302, r.text
    consent = r.headers["location"]
    assert urlparse(consent).path == "/oauth/consent"
    return consent


async def consent(http, consent_url, secret=SECRET, action="allow"):
    request_id = parse_qs(urlparse(consent_url).query)["request"][0]
    return await http.post("/oauth/consent", data={"request": request_id, "secret": secret, "action": action})


async def token(http, **form):
    return await http.post("/token", data=form)


async def login(http):
    """Full browser-style authorization; returns the token response."""
    client_id = await register(http)
    verifier, challenge = pkce()
    url = await start_authorize(http, client_id, challenge)
    r = await consent(http, url)
    code = parse_qs(urlparse(r.headers["location"]).query)["code"][0]
    r = await token(
        http,
        grant_type="authorization_code",
        code=code,
        redirect_uri=REDIRECT,
        client_id=client_id,
        code_verifier=verifier,
    )
    assert r.status_code == 200, r.text
    return client_id, r.json()


async def mcp_initialize(http, access_token):
    return await http.post(
        "/mcp",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json, text/event-stream",
        },
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "t", "version": "0"},
            },
        },
    )


async def test_publishes_oauth_metadata(bridge):
    meta = (await bridge.get("/.well-known/oauth-authorization-server")).json()
    base = str(bridge.base_url).rstrip("/")
    assert meta["issuer"].rstrip("/") == base
    assert meta["authorization_endpoint"] == f"{base}/authorize"
    assert meta["token_endpoint"] == f"{base}/token"
    assert meta["registration_endpoint"] == f"{base}/register"
    assert "S256" in meta["code_challenge_methods_supported"]

    assert meta["scopes_supported"] == ["claude"]
    assert "none" in meta["token_endpoint_auth_methods_supported"]

    prm = await bridge.get("/.well-known/oauth-protected-resource/mcp")
    assert prm.status_code == 200
    assert prm.json()["resource"].rstrip("/") == f"{base}/mcp"
    assert prm.json()["scopes_supported"] == ["claude"]


async def test_mcp_without_a_token_points_the_client_at_oauth(bridge):
    r = await bridge.post("/mcp", json={})
    assert r.status_code == 401
    assert "resource_metadata" in r.headers["www-authenticate"]


async def test_full_login_gives_tokens_that_open_mcp(bridge):
    _, tokens = await login(bridge)
    assert tokens["token_type"].lower() == "bearer"
    assert tokens["refresh_token"]
    assert tokens["scope"] == "claude"
    r = await mcp_initialize(bridge, tokens["access_token"])
    assert r.status_code == 200, r.text


async def test_consent_page_names_the_client_and_escapes_it(bridge):
    client_id = await register(bridge, name="<script>alert(1)</script>")
    _, challenge = pkce()
    url = await start_authorize(bridge, client_id, challenge)
    page = await bridge.get(url)
    assert page.status_code == 200
    assert "&lt;script&gt;" in page.text and "<script>alert" not in page.text
    assert "chatgpt.example" in page.text


async def test_wrong_secret_gives_no_code(bridge):
    client_id = await register(bridge)
    _, challenge = pkce()
    url = await start_authorize(bridge, client_id, challenge)
    r = await consent(bridge, url, secret="guess")
    assert r.status_code == 401
    assert "location" not in r.headers


async def test_a_new_sign_in_attempt_does_not_reset_the_count_of_wrong_secrets(bridge, clock):
    # Anyone may register and start an attempt, so five tries per attempt alone
    # would let a script guess forever by starting a new one every five.
    client_id = await register(bridge)
    _, challenge = pkce()
    codes = []
    for _ in range(6):
        url = await start_authorize(bridge, client_id, challenge)
        codes.append((await consent(bridge, url, secret="guess")).status_code)
    refused = await consent(bridge, await start_authorize(bridge, client_id, challenge))
    clock.t += 601
    allowed = await consent(bridge, await start_authorize(bridge, client_id, challenge))
    assert codes == [401] * 5 + [429] and refused.status_code == 429
    assert allowed.status_code == 302 and "code=" in allowed.headers["location"]


def an_hour_after_now():
    # An app's registration time is the MCP SDK's, read from the real clock while the test
    # runs, so the hour is counted from now and not from when the test clock was made.
    return time.time() + 3601


async def test_an_app_that_never_signs_in_is_forgotten_after_an_hour(bridge, clock):
    # Anyone may register, so an app that never signs in must not be kept for ever.
    # One that did sign in is kept, or its next refresh would fail.
    signed_in, tokens = await login(bridge)
    stranger = await register(bridge, name="never signs in")
    clock.t = an_hour_after_now()
    await register(bridge, name="the next app")  # registering is when the bridge tidies up
    _, challenge = pkce()
    forgotten = await bridge.get("/authorize", params=authorize_params(stranger, challenge))
    refresh = dict(grant_type="refresh_token", client_id=signed_in, refresh_token=tokens["refresh_token"])
    kept = await token(bridge, **refresh)
    assert forgotten.status_code == 400
    assert kept.status_code == 200, kept.text


async def test_registering_stops_while_twenty_apps_wait_to_sign_in(bridge, clock):
    # A script could otherwise register apps without end, each one a row kept on disk.
    await login(bridge)  # an app that signed in is not waiting, so it does not count
    for i in range(20):
        await register(bridge, name=f"waiting {i}")
    refused = await bridge.post("/register", json=registration("one too many"))
    clock.t = an_hour_after_now()
    again = await bridge.post("/register", json=registration("after an hour"))
    assert refused.status_code == 400 and refused.json()["error"] == "invalid_client_metadata"
    assert again.status_code == 201, again.text


async def test_a_sign_in_is_refused_while_twenty_are_open(bridge, clock):
    # Each open sign-in is held in memory for ten minutes, so without a limit one app
    # could fill the memory by asking again and again.
    client_id = await register(bridge)
    _, challenge = pkce()
    for _ in range(20):
        await start_authorize(bridge, client_id, challenge)
    refused = await bridge.get("/authorize", params=authorize_params(client_id, challenge))
    clock.t += 601
    reopened = await start_authorize(bridge, client_id, challenge)
    assert refused.status_code == 302
    assert parse_qs(urlparse(refused.headers["location"]).query)["error"] == ["temporarily_unavailable"]
    assert urlparse(reopened).path == "/oauth/consent"


async def test_too_many_wrong_secrets_burn_the_request(bridge):
    client_id = await register(bridge)
    _, challenge = pkce()
    url = await start_authorize(bridge, client_id, challenge)
    for _ in range(5):
        await consent(bridge, url, secret="guess")
    r = await consent(bridge, url)
    assert r.status_code == 400
    assert "location" not in r.headers


async def test_owner_can_refuse(bridge):
    client_id = await register(bridge)
    _, challenge = pkce()
    url = await start_authorize(bridge, client_id, challenge, state="s-9")
    r = await consent(bridge, url, action="deny")
    assert r.status_code == 302
    q = parse_qs(urlparse(r.headers["location"]).query)
    assert q["error"] == ["access_denied"] and q["state"] == ["s-9"]


async def test_code_needs_the_right_verifier_and_works_once(bridge):
    client_id = await register(bridge)
    verifier, challenge = pkce()
    url = await start_authorize(bridge, client_id, challenge)
    code = parse_qs(urlparse((await consent(bridge, url)).headers["location"]).query)["code"][0]
    form = dict(grant_type="authorization_code", code=code, redirect_uri=REDIRECT, client_id=client_id)

    assert (await token(bridge, **form, code_verifier="wrong" * 10)).status_code == 400
    assert (await token(bridge, **form, code_verifier=verifier)).status_code == 200
    assert (await token(bridge, **form, code_verifier=verifier)).status_code == 400


async def test_refresh_rotates_and_old_refresh_dies(bridge):
    client_id, tokens = await login(bridge)
    form = dict(grant_type="refresh_token", client_id=client_id)

    r = await token(bridge, **form, refresh_token=tokens["refresh_token"])
    assert r.status_code == 200, r.text
    fresh = r.json()
    assert fresh["refresh_token"] != tokens["refresh_token"]
    assert (await mcp_initialize(bridge, fresh["access_token"])).status_code == 200


async def test_a_refresh_token_stays_usable_for_a_minute_after_it_is_spent(bridge, clock):
    """The Claude app refreshed twice at once with the same token; the second must not lock it out."""
    client_id, tokens = await login(bridge)
    form = dict(grant_type="refresh_token", client_id=client_id, refresh_token=tokens["refresh_token"])
    first, second = await token(bridge, **form), await token(bridge, **form)
    assert first.status_code == 200 and second.status_code == 200
    assert (await mcp_initialize(bridge, second.json()["access_token"])).status_code == 200
    clock.t += 61
    assert (await token(bridge, **form)).status_code == 400  # a minute later it is spent for good


async def test_every_token_refresh_is_logged_with_its_outcome(bridge, caplog, clock):
    import logging

    client_id, tokens = await login(bridge)
    form = dict(grant_type="refresh_token", client_id=client_id)
    with caplog.at_level(logging.INFO, logger="claude_voice.oauth"):
        assert (await token(bridge, **form, refresh_token=tokens["refresh_token"])).status_code == 200
        clock.t += 61
        assert (await token(bridge, **form, refresh_token=tokens["refresh_token"])).status_code == 400
    lines = [r.getMessage() for r in caplog.records if r.name == "claude_voice.oauth"]
    assert any(line.startswith(f"oauth refresh ok for client {client_id[:8]}") for line in lines), lines
    assert any(line.startswith(f"oauth refresh refused for client {client_id[:8]}") for line in lines), lines


async def test_access_tokens_expire(bridge, clock):
    _, tokens = await login(bridge)
    clock.t += tokens["expires_in"] + 1
    assert (await mcp_initialize(bridge, tokens["access_token"])).status_code == 401


async def test_the_access_token_lifetime_is_a_setting(tmp_path, root, clock):
    # A client that cannot refresh gives the bridge up; on a computer that sleeps at night,
    # a token that lasts a week needs a refresh once a week instead of after every pause.
    week = 7 * 24 * 3600
    async with served(tmp_path, root, clock, access_ttl=week) as http:
        _, tokens = await login(http)
        assert tokens["expires_in"] == week
        clock.t += 3600 + 1
        assert (await mcp_initialize(http, tokens["access_token"])).status_code == 200
        clock.t += week
        assert (await mcp_initialize(http, tokens["access_token"])).status_code == 401


async def test_the_static_token_still_works_for_local_tools(tmp_path, clock):
    store = Store(tmp_path / "b.db", clock=clock)
    oauth = OAuthProvider(store, SECRET, "http://127.0.0.1:1", clock=clock, static_token="x" * 40)
    assert (await oauth.load_access_token("x" * 40)) is not None
    assert (await oauth.load_access_token("y" * 40)) is None


async def test_tokens_survive_restart_and_are_stored_hashed(tmp_path, clock):
    path = tmp_path / "b.db"
    store = Store(path, clock=clock)
    oauth = OAuthProvider(store, SECRET, "http://127.0.0.1:1", clock=clock)
    issued = await oauth.issue_tokens("client-1", ["claude"], resource=None)

    reopened = OAuthProvider(Store(path, clock=clock), SECRET, "http://127.0.0.1:1", clock=clock)
    assert (await reopened.load_access_token(issued.access_token)) is not None
    raw = path.read_bytes()
    assert issued.access_token.encode() not in raw
    assert issued.refresh_token.encode() not in raw
