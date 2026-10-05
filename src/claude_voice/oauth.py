"""A one-owner OAuth 2.1 authorization server, so ChatGPT can connect.

The MCP SDK supplies the protocol (registration, PKCE, token endpoint,
metadata); this supplies the decisions. Any client may register, but a client
only gets a code after the owner types the login secret on the consent page.
Access and refresh tokens are stored as SHA-256 hashes; refresh tokens rotate.
"""

from __future__ import annotations

import hashlib
import hmac
import html
import json
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlparse

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    RefreshToken,
    TokenError,
    construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .store import Store

ACCESS_TTL = 3600
REFRESH_TTL = 90 * 24 * 3600
CODE_TTL = 300
CONSENT_TTL = 600
MAX_SECRET_ATTEMPTS = 5
SCOPE = "claude"
METADATA_PATH = "/.well-known/oauth-authorization-server"

SCHEMA = """
CREATE TABLE IF NOT EXISTS oauth_clients(client_id TEXT PRIMARY KEY, info TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS oauth_tokens(
  hash TEXT PRIMARY KEY,
  kind TEXT NOT NULL,          -- 'access' | 'refresh'
  client_id TEXT NOT NULL,
  scopes TEXT NOT NULL,
  resource TEXT,
  expires_at REAL NOT NULL,
  family TEXT NOT NULL         -- tokens issued together are revoked together
);
"""


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@dataclass
class _PendingConsent:
    client: OAuthClientInformationFull
    params: AuthorizationParams
    expires_at: float
    attempts: int = 0


class OAuthProvider:
    def __init__(
        self,
        store: Store,
        login_secret: str,
        public_url: str,
        clock: Callable[[], float] = time.time,
        static_token: str | None = None,
    ) -> None:
        self.db = store.db
        self.db.executescript(SCHEMA)
        self.login_secret = login_secret
        self.public_url = public_url.rstrip("/")
        self.clock = clock
        self.static_token = static_token
        self._consents: dict[str, _PendingConsent] = {}
        self._codes: dict[str, AuthorizationCode] = {}

    # -- clients -------------------------------------------------------------

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        row = self.db.execute("SELECT info FROM oauth_clients WHERE client_id=?", (client_id,)).fetchone()
        return OAuthClientInformationFull.model_validate_json(row[0]) if row else None

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        with self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO oauth_clients VALUES(?,?)",
                (client_info.client_id, client_info.model_dump_json()),
            )

    # -- authorization -------------------------------------------------------

    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        self._expire()
        request_id = secrets.token_urlsafe(24)
        self._consents[request_id] = _PendingConsent(client, params, self.clock() + CONSENT_TTL)
        return f"{self.public_url}/oauth/consent?request={request_id}"

    def _expire(self) -> None:
        now = self.clock()
        for key in [k for k, c in self._consents.items() if c.expires_at < now]:
            del self._consents[key]
        for key in [k for k, c in self._codes.items() if c.expires_at < now]:
            del self._codes[key]

    async def consent_page(self, request: Request) -> Response:
        self._expire()
        pending = self._consents.get(request.query_params.get("request", ""))
        if pending is None:
            return HTMLResponse(_page("This sign-in request has expired. Start again."), 400)
        return HTMLResponse(_consent_form(request.query_params["request"], pending))

    async def consent_submit(self, request: Request) -> Response:
        self._expire()
        form = await request.form()
        request_id = str(form.get("request", ""))
        pending = self._consents.get(request_id)
        if pending is None or pending.attempts >= MAX_SECRET_ATTEMPTS:
            self._consents.pop(request_id, None)
            return HTMLResponse(_page("This sign-in request is no longer valid."), 400)

        params = pending.params
        if form.get("action") == "deny":
            del self._consents[request_id]
            return RedirectResponse(
                construct_redirect_uri(str(params.redirect_uri), error="access_denied", state=params.state),
                302,
            )

        if not hmac.compare_digest(str(form.get("secret", "")).encode(), self.login_secret.encode()):
            pending.attempts += 1
            left = MAX_SECRET_ATTEMPTS - pending.attempts
            return HTMLResponse(_consent_form(request_id, pending, error=f"Wrong secret. {left} tries left."), 401)

        del self._consents[request_id]
        code = secrets.token_urlsafe(32)
        self._codes[code] = AuthorizationCode(
            code=code,
            scopes=params.scopes or [SCOPE],
            expires_at=self.clock() + CODE_TTL,
            client_id=pending.client.client_id,
            code_challenge=params.code_challenge,
            redirect_uri=params.redirect_uri,
            redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
            resource=params.resource,
            subject="owner",
        )
        return RedirectResponse(construct_redirect_uri(str(params.redirect_uri), code=code, state=params.state), 302)

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        self._expire()
        code = self._codes.get(authorization_code)
        if code is None or code.client_id != client.client_id:
            return None
        return code

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        if self._codes.pop(authorization_code.code, None) is None:
            raise TokenError("invalid_grant", "authorization code already used")
        return await self.issue_tokens(client.client_id, authorization_code.scopes, authorization_code.resource)

    # -- tokens --------------------------------------------------------------

    async def issue_tokens(
        self, client_id: str, scopes: list[str], resource: str | None, family: str | None = None
    ) -> OAuthToken:
        now = self.clock()
        family = family or secrets.token_hex(8)
        access, refresh = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        rows = [
            (
                _hash(access),
                "access",
                client_id,
                json.dumps(scopes),
                resource,
                now + ACCESS_TTL,
                family,
            ),
            (
                _hash(refresh),
                "refresh",
                client_id,
                json.dumps(scopes),
                resource,
                now + REFRESH_TTL,
                family,
            ),
        ]
        with self.db:
            self.db.execute("DELETE FROM oauth_tokens WHERE expires_at < ?", (now,))
            self.db.executemany("INSERT INTO oauth_tokens VALUES(?,?,?,?,?,?,?)", rows)
        return OAuthToken(
            access_token=access,
            expires_in=ACCESS_TTL,
            refresh_token=refresh,
            scope=" ".join(scopes) or None,
        )

    def _load(self, token: str, kind: str):
        row = self.db.execute(
            "SELECT client_id, scopes, resource, expires_at, family FROM oauth_tokens WHERE hash=? AND kind=?",
            (_hash(token), kind),
        ).fetchone()
        if row is None or row[3] < self.clock():
            return None
        return row

    async def load_access_token(self, token: str) -> AccessToken | None:
        if self.static_token and hmac.compare_digest(token.encode(), self.static_token.encode()):
            return AccessToken(token=token, client_id="static-token", scopes=[SCOPE], subject="owner")
        row = self._load(token, "access")
        if row is None:
            return None
        client_id, scopes, resource, expires_at, _ = row
        return AccessToken(
            token=token,
            client_id=client_id,
            scopes=json.loads(scopes),
            expires_at=int(expires_at),
            resource=resource,
            subject="owner",
        )

    async def load_refresh_token(self, client: OAuthClientInformationFull, refresh_token: str) -> RefreshToken | None:
        row = self._load(refresh_token, "refresh")
        if row is None or row[0] != client.client_id:
            return None
        client_id, scopes, resource, expires_at, _ = row
        return RefreshToken(
            token=refresh_token,
            client_id=client_id,
            scopes=json.loads(scopes),
            expires_at=int(expires_at),
            resource=resource,
            subject="owner",
        )

    async def exchange_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: RefreshToken, scopes: list[str]
    ) -> OAuthToken:
        row = self._load(refresh_token.token, "refresh")
        family = row[4] if row else None
        with self.db:
            # Rotation: the presented refresh token is spent.
            self.db.execute("DELETE FROM oauth_tokens WHERE hash=?", (_hash(refresh_token.token),))
        return await self.issue_tokens(client.client_id, scopes or refresh_token.scopes, refresh_token.resource, family)

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        row = self.db.execute("SELECT family FROM oauth_tokens WHERE hash=?", (_hash(token.token),)).fetchone()
        if row:
            with self.db:
                self.db.execute("DELETE FROM oauth_tokens WHERE family=?", (row[0],))

    async def exchange_identity_assertion(self, *args, **kwargs):  # pragma: no cover
        raise NotImplementedError


class PublicClientMetadata:
    """Advertise `none` (PKCE-only public clients) in the server metadata.

    The SDK registers such clients fine but lists only secret-based methods,
    which can make a client like ChatGPT give up before registering.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["path"] != METADATA_PATH:
            await self.app(scope, receive, send)
            return
        start: Message = {}
        body = b""

        async def capture(message: Message) -> None:
            nonlocal start, body
            if message["type"] == "http.response.start":
                start = message
            else:
                body += message.get("body", b"")

        await self.app(scope, receive, capture)
        if start.get("status") == 200:
            meta = json.loads(body)
            methods = meta.setdefault("token_endpoint_auth_methods_supported", [])
            if "none" not in methods:
                methods.append("none")
            body = json.dumps(meta).encode()
            headers = [(k, v) for k, v in start["headers"] if k.lower() != b"content-length"]
            start = {**start, "headers": [*headers, (b"content-length", str(len(body)).encode())]}
        await send(start)
        await send({"type": "http.response.body", "body": body})


def _page(body: str) -> str:
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        "<title>claude-voice sign-in</title>"
        "<style>body{font:16px system-ui;max-width:28rem;margin:2rem auto;padding:0 1rem}"
        "input,button{font:inherit;padding:.6rem;width:100%;box-sizing:border-box;margin:.3rem 0}"
        ".err{color:#b00}</style></head><body>" + body + "</body></html>"
    )


def _consent_form(request_id: str, pending: _PendingConsent, error: str | None = None) -> str:
    name = html.escape(pending.client.client_name or pending.client.client_id or "unknown client")
    host = html.escape(urlparse(str(pending.params.redirect_uri)).hostname or "")
    err = f"<p class='err'>{html.escape(error)}</p>" if error else ""
    rid = html.escape(request_id)
    return _page(
        f"<h1>Allow {name}?</h1>"
        f"<p>It wants to control Claude Code on this machine and will return to <b>{host}</b>.</p>"
        f"{err}<form method='post' action='/oauth/consent'>"
        f"<input type='hidden' name='request' value='{rid}'>"
        "<input type='password' name='secret' placeholder='Login secret' autofocus>"
        "<button name='action' value='allow'>Allow</button>"
        "<button name='action' value='deny'>Deny</button></form>"
    )
