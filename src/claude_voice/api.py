"""The event API: what a companion app, such as claude-voice-ui, reads the bridge through.

A few JSON endpoints and one stream of server-sent events, under /api/v1, behind the
bridge's own login: a bearer token (the login secret or an OAuth access token), or a
sign-in cookie for a page the bridge serves itself. docs/event-api.md is the contract.

The bridge's state is handed in as callables, so this module knows nothing about how
sessions are found; the event hub is shared with the request middleware, which
publishes a `focus` event whenever a tool is aimed at a session.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import hmac
import ipaddress
import json
import secrets
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.types import ASGIApp, Receive, Scope, Send

COOKIE = "cv_ui"
SIGN_IN_SECONDS = 30 * 24 * 3600
MAX_FAILURES = 5
FAILURE_WINDOW = 600.0
KEEP_ALIVE_SECONDS = 15.0
NEWS_SECONDS = 2.0

# Tools whose `session`, `session_id` or `name` argument names the session a caller reached.
SESSION_TOOLS = {
    "ask_active_session",
    "message_active_session",
    "read_session_output",
    "search_session_history",
    "digest_session",
    "side_question",
    "send_task",
    "session_recap",
    "cancel",
    "stop_active_session",
}


TAILNET = (ipaddress.ip_network("100.64.0.0/10"), ipaddress.ip_network("fd7a:115c:a1e0::/48"))
PRIVATE_PATHS = ("/api/", "/ui")


def caller_address(scope: Scope) -> str:
    """Where a request really comes from. Tailscale, for Funnel and for the tailnet port
    alike, connects from this computer and adds the caller to X-Forwarded-For; anything a
    caller wrote there itself comes before that, so only the last entry counts."""
    for name, value in scope.get("headers") or []:
        if name == b"x-forwarded-for":
            last = value.decode("latin-1").split(",")[-1].strip()
            if last:
                return last
    client = scope.get("client") or ("", 0)
    return str(client[0])


def from_tailnet_or_here(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    return ip.is_loopback or any(ip in net for net in TAILNET)


class TailnetOnly:
    """Refuses the event API and the companion app's files to anyone but this computer and
    the tailnet, so they cannot be reached through Funnel; everything else passes."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = str(scope.get("path", ""))
        # RULE: the event API answers only this computer and the tailnet
        if scope.get("type") == "http" and path.startswith(PRIVATE_PATHS):
            if not from_tailnet_or_here(caller_address(scope)):
                response = JSONResponse({"error": "only reachable from this computer or the tailnet"}, 403)
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


def _scheme(request: Request) -> str:
    """The scheme the caller used: Tailscale says it in X-Forwarded-Proto, last entry counting."""
    forwarded = request.headers.get("x-forwarded-proto", "").split(",")[-1].strip().lower()
    return forwarded or request.url.scheme


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class EventHub:
    """Hands each published event to every open stream; a slow stream loses its oldest."""

    def __init__(self, keep: int = 100) -> None:
        self.keep = keep
        self._queues: set[asyncio.Queue[tuple[str, dict[str, Any] | None]]] = set()

    def publish(self, event: str, data: dict[str, Any] | None) -> None:
        for q in list(self._queues):
            if q.full():
                with contextlib.suppress(asyncio.QueueEmpty):
                    q.get_nowait()
            q.put_nowait((event, data))

    def listening(self) -> bool:
        """Whether any stream is open, so work done only to tell someone can be skipped."""
        return bool(self._queues)

    @contextlib.contextmanager
    def subscribe(self) -> Any:
        q: asyncio.Queue[tuple[str, dict[str, Any] | None]] = asyncio.Queue(self.keep)
        self._queues.add(q)
        try:
            yield q
        finally:
            self._queues.discard(q)


HUB = EventHub()


def publish_focus(tool: str, arguments: Any) -> None:
    """Called for every tool call: tell open streams which session the caller reached."""
    if tool not in SESSION_TOOLS or not isinstance(arguments, dict):
        return
    session = arguments.get("session") or arguments.get("session_id") or arguments.get("name")
    if session:
        HUB.publish("focus", {"session": str(session), "tool": tool, "at": now_iso()})


class EventApi:
    def __init__(
        self,
        store: Any,
        login_secret: str,
        public_url: str,
        check_token: Callable[[str], Awaitable[bool]],
        sessions: Callable[[], dict[str, Any]],
        get_active: Callable[[], dict[str, Any] | None],
        set_active: Callable[[str, str], dict[str, Any]],
        output: Callable[[str, int, int | None], dict[str, Any]],
        pending: Callable[[str], dict[str, Any]],
        prs: Callable[[str], Awaitable[dict[str, Any]]],
        news: Callable[[str | None], dict[str, Any]],
        roles: Callable[[], dict[str, Any]] | None = None,
        start_role: Callable[[str, str | None], Awaitable[dict[str, Any]]] | None = None,
        clear_active: Callable[[], None] | None = None,
        doctor: Callable[[], dict[str, Any]] | None = None,
        usage: Callable[[], dict[str, Any]] | None = None,
        update_state: Callable[[], dict[str, Any]] | None = None,
        start_update: Callable[[], dict[str, Any]] | None = None,
        bridge: Callable[[], dict[str, Any]] | None = None,
        ui_origins: list[str] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.store = store
        self.login_secret = login_secret
        self.public_url = public_url.rstrip("/")
        self.check_token = check_token
        self.sessions, self.get_active, self.set_active = sessions, get_active, set_active
        self.clear_active = clear_active
        self.doctor = doctor
        self.usage = usage
        self.update_state, self.start_update, self.bridge = update_state, start_update, bridge
        self.output, self.pending, self.prs, self.news = output, pending, prs, news
        self.roles = roles or (lambda: {"roles": []})
        self.start_role = start_role
        self.ui_origins = [o.rstrip("/") for o in ui_origins or []]
        self.clock = clock
        self._failures: list[float] = []

    def routes(self, mcp: Any) -> None:
        route = mcp.custom_route
        route("/api/v1/signin", methods=["POST"])(self.signin)
        route("/api/v1/signout", methods=["POST"])(self.signout)
        route("/api/v1/sessions", methods=["GET"])(self.list_sessions)
        route("/api/v1/active", methods=["GET", "PUT", "DELETE"])(self.active)
        route("/api/v1/sessions/{session}/output", methods=["GET"])(self.session_output)
        route("/api/v1/sessions/{session}/pending", methods=["GET"])(self.session_pending)
        route("/api/v1/sessions/{session}/prs", methods=["GET"])(self.session_prs)
        route("/api/v1/events", methods=["GET"])(self.events)
        route("/api/v1/roles", methods=["GET"])(self.list_roles)
        route("/api/v1/doctor", methods=["GET"])(self.dependencies)
        route("/api/v1/usage", methods=["GET"])(self.usage_now)
        route("/api/v1/update", methods=["GET", "POST"])(self.update)
        route("/api/v1/bridge", methods=["GET"])(self.bridge_now)
        route("/api/v1/roles/{role}/start", methods=["POST"])(self.role_start)

    # -- who is asking ---------------------------------------------------------------

    async def _bearer(self, request: Request) -> bool:
        auth = request.headers.get("authorization", "")
        return auth.startswith("Bearer ") and await self.check_token(auth[len("Bearer ") :])

    def _cookie(self, request: Request) -> bool:
        token = request.cookies.get(COOKIE)
        return token is not None and self.store.web_session_valid(_hash(token))

    async def _allowed(self, request: Request) -> bool:
        return await self._bearer(request) or self._cookie(request)

    def _same_site(self, request: Request) -> bool:
        origin = (request.headers.get("origin") or "").rstrip("/")
        own = (self.public_url, f"{request.url.scheme}://{request.url.netloc}")
        return bool(origin) and (origin in own or origin in self.ui_origins)

    async def _change_allowed(self, request: Request) -> Response | None:
        """None when the caller may change something; otherwise the refusal."""
        if await self._bearer(request):
            return None  # a bearer token is not sent by a browser on its own
        if not self._cookie(request):
            return JSONResponse({"error": "sign in first"}, 401)
        # RULE: a change sent from another site is refused
        if not self._same_site(request):  # a page elsewhere cannot change the choice
            return JSONResponse({"error": "not from an allowed page"}, 403)
        return None

    # -- signing in ------------------------------------------------------------------

    async def signin(self, request: Request) -> Response:
        now = self.clock()
        self._failures = [t for t in self._failures if now - t < FAILURE_WINDOW]
        # RULE: guessing the secret on the page stops after five wrong tries
        if len(self._failures) >= MAX_FAILURES:
            return JSONResponse({"error": "too many wrong tries; wait ten minutes"}, 429)
        try:
            body = await request.json()
        except ValueError:
            body = {}
        secret = str(body.get("secret", "")) if isinstance(body, dict) else ""
        if not hmac.compare_digest(secret.encode(), self.login_secret.encode()):
            self._failures.append(now)
            return JSONResponse({"error": "wrong secret"}, 401)
        token = secrets.token_urlsafe(32)
        self.store.add_web_session(_hash(token), SIGN_IN_SECONDS)
        response = Response(status_code=204)
        response.set_cookie(
            COOKIE,
            token,
            max_age=SIGN_IN_SECONDS,
            httponly=True,
            samesite="strict",
            secure=_scheme(request) == "https",  # a browser drops a secure cookie on a plain http page
            path="/api",
        )
        return response

    async def signout(self, request: Request) -> Response:
        refused = await self._change_allowed(request)
        if refused:
            return refused
        token = request.cookies.get(COOKIE)
        if token:
            self.store.drop_web_session(_hash(token))
        response = Response(status_code=204)
        response.delete_cookie(COOKIE, path="/api")
        return response

    # -- reading and choosing --------------------------------------------------------

    async def list_sessions(self, request: Request) -> Response:
        if not await self._allowed(request):
            return JSONResponse({"error": "sign in first"}, 401)
        return JSONResponse({**self.sessions(), "active": self.get_active()}, headers={"Cache-Control": "no-store"})

    async def active(self, request: Request) -> Response:
        if request.method == "GET":
            if not await self._allowed(request):
                return JSONResponse({"error": "sign in first"}, 401)
            return JSONResponse(self.get_active())
        refused = await self._change_allowed(request)
        if refused:
            return refused
        if request.method == "DELETE":
            if self.clear_active is not None:
                self.clear_active()
            return Response(status_code=204)
        try:
            body = await request.json()
        except ValueError:
            body = {}
        try:
            return JSONResponse(self.set_active(str((body or {}).get("session", "")), "page"))
        except LookupError as exc:
            return JSONResponse({"error": str(exc)}, 404)

    async def _one(self, request: Request, read: Callable[[str], Any]) -> Response:
        if not await self._allowed(request):
            return JSONResponse({"error": "sign in first"}, 401)
        try:
            out = read(request.path_params["session"])
            if asyncio.iscoroutine(out):
                out = await out
        except LookupError as exc:
            return JSONResponse({"error": str(exc)}, 404)
        return JSONResponse(out, headers={"Cache-Control": "no-store"})

    async def session_output(self, request: Request) -> Response:
        limit = int(request.query_params.get("limit", "6") or 6)
        after = request.query_params.get("after")
        return await self._one(request, lambda s: self.output(s, limit, int(after) if after else None))

    async def session_pending(self, request: Request) -> Response:
        return await self._one(request, self.pending)

    async def session_prs(self, request: Request) -> Response:
        return await self._one(request, self.prs)

    async def dependencies(self, request: Request) -> Response:
        if not await self._allowed(request):
            return JSONResponse({"error": "sign in first"}, 401)
        if self.doctor is None:
            return JSONResponse({"error": "no doctor here"}, 404)
        report = await asyncio.to_thread(self.doctor)
        return JSONResponse(report, headers={"Cache-Control": "no-store"})

    async def usage_now(self, request: Request) -> Response:
        if not await self._allowed(request):
            return JSONResponse({"error": "sign in first"}, 401)
        if self.usage is None:
            return JSONResponse({"error": "no usage here"}, 404)
        return JSONResponse(self.usage(), headers={"Cache-Control": "no-store"})

    async def update(self, request: Request) -> Response:
        if self.update_state is None or self.start_update is None:
            return JSONResponse({"error": "no updates here"}, 404)
        if request.method == "GET":
            if not await self._allowed(request):
                return JSONResponse({"error": "sign in first"}, 401)
            return JSONResponse(self.update_state(), headers={"Cache-Control": "no-store"})
        refused = await self._change_allowed(request)
        if refused:
            return refused
        try:
            return JSONResponse(self.start_update(), status_code=202)
        except RuntimeError as exc:
            return JSONResponse({"error": str(exc)}, 409)
        except LookupError as exc:
            return JSONResponse({"error": str(exc)}, 404)

    async def bridge_now(self, request: Request) -> Response:
        if not await self._allowed(request):
            return JSONResponse({"error": "sign in first"}, 401)
        if self.bridge is None:
            return JSONResponse({"error": "not here"}, 404)
        return JSONResponse(self.bridge(), headers={"Cache-Control": "no-store"})

    # -- roles -----------------------------------------------------------------------

    async def list_roles(self, request: Request) -> Response:
        if not await self._allowed(request):
            return JSONResponse({"error": "sign in first"}, 401)
        return JSONResponse(self.roles(), headers={"Cache-Control": "no-store"})

    async def role_start(self, request: Request) -> Response:
        refused = await self._change_allowed(request)
        if refused:
            return refused
        if self.start_role is None:
            return JSONResponse({"error": "no roles here"}, 404)
        try:
            body = await request.json()
        except ValueError:
            body = {}
        task = (body or {}).get("task")
        try:
            return JSONResponse(await self.start_role(request.path_params["role"], str(task) if task else None))
        except LookupError as exc:
            return JSONResponse({"error": str(exc)}, 404)
        except Exception as exc:  # the start failed; say why
            return JSONResponse({"error": str(getattr(exc, "message", exc))}, 502)

    # -- the live stream -------------------------------------------------------------

    async def events(self, request: Request) -> Response:
        if not await self._allowed(request):
            return JSONResponse({"error": "sign in first"}, 401)
        limit = int(request.query_params.get("max_events", "0") or 0)  # 0: until the caller leaves
        return StreamingResponse(
            self._stream(limit), media_type="text/event-stream", headers={"Cache-Control": "no-store"}
        )

    async def _stream(self, limit: int) -> AsyncIterator[str]:
        sent = 0
        cursor = self.news(None)["cursor"]
        quiet = 0.0
        with HUB.subscribe() as queue:
            yield ": connected\n\n"
            while not limit or sent < limit:
                try:
                    event, data = await asyncio.wait_for(queue.get(), NEWS_SECONDS)
                    yield f"event: {event}\ndata: {json.dumps(data)}\n\n"
                    sent += 1
                    continue
                except TimeoutError:
                    pass
                news = self.news(cursor)
                cursor = news["cursor"]
                for item in news["events"]:
                    yield f"event: news\ndata: {json.dumps(item, default=str)}\n\n"
                    sent += 1
                quiet += NEWS_SECONDS
                if quiet >= KEEP_ALIVE_SECONDS:
                    quiet = 0.0
                    yield ": keep-alive\n\n"
