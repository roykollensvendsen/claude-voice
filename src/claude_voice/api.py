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
import logging
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

# Tools that talk to a session, by its `session`, `session_id` or `name` argument: they
# put it in focus, the session the voice is talking to now.
TALK_TOOLS = {
    "ask_active_session",
    "message_active_session",
    "send_task",
    "cancel",
    "stop_active_session",
    "rename_session",
}
# Tools that only look at a session: a quieter `peek`, so a look never reads as talking.
LOOK_TOOLS = {"read_session_output", "search_session_history", "digest_session", "side_question", "session_recap"}
SESSION_TOOLS = TALK_TOOLS | LOOK_TOOLS


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


LOCAL_NAMES = ("127.0.0.1", "localhost")
FORWARDING = (b"x-forwarded-for", b"forwarded")


def _loopback(address: str) -> bool:
    try:
        return ipaddress.ip_address(address).is_loopback
    except ValueError:
        return False


def _header(headers: list[tuple[bytes, bytes]], wanted: bytes) -> str | None:
    """The first value of header `wanted`, or None when the request has none."""
    return next((v.decode("latin-1") for n, v in headers if n == wanted), None)


def _forwarded(headers: list[tuple[bytes, bytes]]) -> bool:
    """Whether something, Tailscale included, says it passed the request on."""
    return any(name in FORWARDING or name.startswith(b"tailscale-user-") for name, _ in headers)


def _own(port: Any, scheme: str = "") -> set[str]:
    """The bridge's own local addresses on `port`, each after `scheme`."""
    return {f"{scheme}{name}:{port}" for name in LOCAL_NAMES}


def from_here(scope: Scope) -> bool:
    """Whether a request comes from this computer itself (ADR-CV-012). Tailscale and a
    preview server forward the phone's requests from this computer too, so the peer alone
    is not enough: nothing may have forwarded it, it must be addressed to the bridge's own
    local address, and a page that sent it must be the bridge's own."""
    peer = str((scope.get("client") or ("", 0))[0])
    port = (scope.get("server") or ("", None))[1]
    headers = scope.get("headers") or []
    # RULE: a request from another computer is not from here
    if not _loopback(peer):
        return False
    # RULE: a forwarded request is not from here
    if _forwarded(headers):
        return False
    # RULE: a request to another address is not from here
    if _header(headers, b"host") not in _own(port):
        return False
    origin = _header(headers, b"origin")
    # RULE: a request sent by another website is not from here
    return origin is None or origin in _own(port, "http://")


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
# When this bridge started: a stream that sees it change knows the bridge restarted.
STARTED_AT = datetime.now(UTC).isoformat(timespec="seconds")
log = logging.getLogger("claude_voice")


def session_of(tool: str, arguments: Any) -> str | None:
    """The session a tool call is aimed at, if it is aimed at one."""
    if tool not in SESSION_TOOLS or not isinstance(arguments, dict):
        return None
    session = arguments.get("session") or arguments.get("session_id") or arguments.get("name")
    return str(session) if session else None


def publish_focus(tool: str, arguments: Any, client: str | None = None) -> None:
    """Called for every tool call: tell open streams which session the caller talked to,
    or only looked at."""
    session = session_of(tool, arguments)
    if session is None:
        return
    # RULE: only talking to a session puts it in focus and a look at it does not
    event = "focus" if tool in TALK_TOOLS else "peek"
    HUB.publish(event, {"session": session, "tool": tool, "client": client, "at": now_iso()})


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
        output: Callable[[str, int, int | None], dict[str, Any] | Awaitable[dict[str, Any]]],
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
        settings: Any = None,
        software: Callable[[], dict[str, Any]] | None = None,
        work: Callable[[], dict[str, Any]] | None = None,
        detail: Callable[[str], dict[str, Any]] | None = None,
        summary: Callable[[str, str | None], Awaitable[dict[str, Any]]] | None = None,
        update_changes: Callable[[], dict[str, Any]] | None = None,
        ui_origins: list[str] | None = None,
        remote_prompts: Callable[[], list[dict[str, Any]]] | None = None,
        answer_remote: Callable[[str, dict[str, Any]], Awaitable[tuple[int, dict[str, Any]]]] | None = None,
        focus: Callable[[str], tuple[int, dict[str, str]]] | None = None,
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
        self.settings = settings  # how new sessions start: launch.Settings
        self.software = software
        self.work = work  # what waits on the owner, and the open pull requests: work.Board.report
        self.detail, self.summary = detail, summary
        self.update_changes = update_changes
        self.prompts: dict[str, dict[str, Any]] = {}  # open questions and approvals, by id
        # Questions on other machines, and how to answer one (fleet.Fleet); none without machines.
        self.remote_prompts = remote_prompts or (lambda: [])
        self.answer_remote = answer_remote
        self.focus = focus  # bring a session's terminal to the front: terminals.Terminals, by id
        self.clock = clock
        self._failures: list[float] = []

    def routes(self, mcp: Any) -> None:
        route = mcp.custom_route
        route("/api/v1/signin", methods=["POST"])(self.signin)
        route("/api/v1/signout", methods=["POST"])(self.signout)
        route("/api/v1/sessions", methods=["GET"])(self.list_sessions)
        route("/api/v1/active", methods=["GET", "PUT", "DELETE"])(self.active)
        route("/api/v1/sessions/{session:path}/output", methods=["GET"])(self.session_output)
        route("/api/v1/sessions/{session:path}/pending", methods=["GET"])(self.session_pending)
        route("/api/v1/sessions/{session:path}/prs", methods=["GET"])(self.session_prs)
        route("/api/v1/sessions/{session:path}/detail", methods=["GET"])(self.session_detail)
        route("/api/v1/sessions/{session:path}/summary", methods=["POST"])(self.session_summary)
        route("/api/v1/sessions/{session:path}/focus", methods=["POST"])(self.session_focus)
        route("/api/v1/whoami", methods=["GET"])(self.whoami)
        route("/api/v1/events", methods=["GET"])(self.events)
        route("/api/v1/roles", methods=["GET"])(self.list_roles)
        route("/api/v1/prompts", methods=["GET", "POST"])(self.prompts_route)
        route("/api/v1/settings", methods=["GET", "PUT"])(self.settings_route)
        route("/api/v1/prompts/{prompt}/wait", methods=["GET"])(self.wait_prompt)
        route("/api/v1/prompts/{prompt}/close", methods=["POST"])(self.close_prompt)
        # Last, and taking a slash: a question on another machine is "lukas-laptop/3".
        route("/api/v1/prompts/{prompt:path}", methods=["POST"])(self.answer_prompt)
        route("/api/v1/doctor", methods=["GET"])(self.dependencies)
        route("/api/v1/usage", methods=["GET"])(self.usage_now)
        route("/api/v1/update", methods=["GET", "POST"])(self.update)
        route("/api/v1/health/software", methods=["GET"])(self.software_health)
        route("/api/v1/work", methods=["GET"])(self.work_now)
        route("/api/v1/update/changes", methods=["GET"])(self.changes)
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

    async def session_detail(self, request: Request) -> Response:
        if self.detail is None:
            return JSONResponse({"error": "not available"}, 404)
        return await self._one(request, self.detail)

    async def whoami(self, request: Request) -> Response:
        """Whether the page asking is on this computer itself, so it may offer to open a
        session's terminal. It says nothing about the bridge, so it needs no signing in."""
        return JSONResponse({"here": from_here(request.scope)}, headers={"Cache-Control": "no-store"})

    async def session_focus(self, request: Request) -> Response:
        """Bring a session's terminal to the front on this computer (ADR-CV-012)."""
        if not await self._allowed(request):
            return JSONResponse({"error": "sign in first"}, 401)
        # RULE: only a request from here may bring a terminal to the front
        if not from_here(request.scope):
            return JSONResponse({"reason": "not_here"}, 403)
        if self.focus is None:
            return JSONResponse({"reason": "no_window_list"}, 501)
        # The desktop is asked through programs that may take a moment; the bridge keeps answering.
        status, body = await asyncio.to_thread(self.focus, request.path_params["session"])
        return JSONResponse(body, status, headers={"Cache-Control": "no-store"})

    async def session_summary(self, request: Request) -> Response:
        """Ask for a fresh summary: 202 at once; the stream says when it is ready."""
        if self.summary is None:
            return JSONResponse({"error": "not available"}, 404)
        refused = await self._change_allowed(request)
        if refused:
            return refused
        try:
            body = await request.json()
        except ValueError:
            body = {}
        language = body.get("language") if isinstance(body, dict) else None
        try:
            out = await self.summary(request.path_params["session"], str(language) if language else None)
        except LookupError as exc:
            return JSONResponse({"error": str(exc)}, 404)
        return JSONResponse(out, 202)

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

    async def changes(self, request: Request) -> Response:
        """The commits an update would bring, grouped by kind."""
        if not await self._allowed(request):
            return JSONResponse({"error": "sign in first"}, 401)
        if self.update_changes is None:
            return JSONResponse({"error": "no updates here"}, 404)
        try:
            out = await asyncio.to_thread(self.update_changes)  # a fetch from GitHub takes a moment
        except LookupError as exc:
            return JSONResponse({"error": str(exc)}, 404)
        return JSONResponse(out, headers={"Cache-Control": "no-store"})

    async def software_health(self, request: Request) -> Response:
        """How both repositories are doing: their quality measures, trends and checks."""
        if not await self._allowed(request):
            return JSONResponse({"error": "sign in first"}, 401)
        if self.software is None:
            return JSONResponse({"error": "not available"}, 404)
        out = await asyncio.to_thread(self.software)  # git history and GitHub take a moment
        return JSONResponse(out, headers={"Cache-Control": "no-store"})

    async def work_now(self, request: Request) -> Response:
        """What waits on the owner, read fresh, and every open pull request, kept a minute."""
        if not await self._allowed(request):
            return JSONResponse({"error": "sign in first"}, 401)
        if self.work is None:
            return JSONResponse({"error": "not available"}, 404)
        out = await asyncio.to_thread(self.work)  # GitHub takes a moment
        return JSONResponse(out, headers={"Cache-Control": "no-store"})

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

    # -- how new sessions start --------------------------------------------------------

    async def settings_route(self, request: Request) -> Response:
        if self.settings is None:
            return JSONResponse({"error": "not available"}, 404)
        if request.method == "GET":
            if not await self._allowed(request):
                return JSONResponse({"error": "sign in first"}, 401)
            return JSONResponse(self.settings.get(), headers={"Cache-Control": "no-store"})
        refused = await self._change_allowed(request)
        if refused:
            return refused
        try:
            body = await request.json()
        except ValueError:
            body = None
        # RULE: a setting that does not exist or does not fit is refused
        try:
            now = self.settings.change(body)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, 400)
        log.info("settings -> %s", json.dumps({k: now[k] for k in ("remote_control", "start_in")}))
        HUB.publish("settings", now)
        return JSONResponse(now)

    # -- questions and approvals from the phone -------------------------------------

    @staticmethod
    def _readable(tool: str, tool_input: Any) -> tuple[str, bool]:
        """What an approval would run, short enough to read before saying yes."""
        if not isinstance(tool_input, dict):
            text = str(tool_input)
        elif tool == "Bash":
            text = str(tool_input.get("command", ""))
        elif "file_path" in tool_input:
            text = str(tool_input["file_path"])
        else:
            text = json.dumps(tool_input, ensure_ascii=False)
        return (text[:300], True) if len(text) > 300 else (text, False)

    def _public(self, prompt: dict[str, Any]) -> dict[str, Any]:
        return {k: v for k, v in prompt.items() if k not in ("done", "answer", "raw_input")}

    async def prompts_route(self, request: Request) -> Response:
        if request.method == "GET":
            if not await self._allowed(request):
                return JSONResponse({"error": "sign in first"}, 401)
            open_ = [self._public(p) for p in self.prompts.values() if p["answer"] is None]
            open_ += self.remote_prompts()
            return JSONResponse({"prompts": open_}, headers={"Cache-Control": "no-store"})
        if not await self._bearer(request):  # only the hook on this computer opens prompts
            return JSONResponse({"error": "sign in first"}, 401)
        body = await request.json()
        kind = body.get("kind")
        prompt: dict[str, Any] = {
            "id": secrets.token_hex(8),
            "session": str(body.get("session_id", "")),
            "kind": kind,
            "asked_at": now_iso(),
            "answer": None,
            "done": asyncio.Event(),
        }
        if kind == "question":
            first = ((body.get("question") or {}).get("questions") or [{}])[0]
            prompt["question"] = {
                "text": str(first.get("question", "")),
                "header": str(first.get("header", "")),
                "options": [
                    {"label": str(o.get("label", "")), "description": str(o.get("description", ""))}
                    for o in first.get("options") or []
                    if isinstance(o, dict)
                ],
                "multi": bool(first.get("multiSelect")),
            }
        elif kind == "approval":
            prompt["tool"] = str(body.get("tool", ""))
            prompt["input"], prompt["cut"] = self._readable(prompt["tool"], body.get("input"))
        else:
            return JSONResponse({"error": "kind is question or approval"}, 400)
        self.prompts[prompt["id"]] = prompt
        HUB.publish("prompt", self._public(prompt))
        # Wait for the phone only while someone has the app open; else the box shows at once.
        return JSONResponse({"id": prompt["id"], "wait": HUB.listening()})

    def _fits(self, prompt: dict[str, Any], body: dict[str, Any]) -> str | None:
        """Why an answer does not fit its prompt, or None."""
        if prompt["kind"] == "approval":
            if body.get("decision") not in ("allow", "deny"):
                return "an approval is answered with decision allow or deny"
            return None
        answer = body.get("answer")
        if answer is None:
            return "a question is answered with answer"
        if isinstance(answer, dict):
            return None if str(answer.get("text", "")).strip() else "a free answer needs text"
        labels = [o["label"] for o in prompt["question"]["options"]]
        chosen = answer if isinstance(answer, list) else [answer]
        if len(chosen) > 1 and not prompt["question"]["multi"]:
            return "this question takes one choice"
        unknown = [c for c in chosen if c not in labels]
        return f"not an option: {', '.join(map(str, unknown))}" if unknown else None

    async def answer_prompt(self, request: Request) -> Response:
        refused = await self._change_allowed(request)
        if refused:
            return refused
        prompt = self.prompts.get(request.path_params["prompt"])
        if prompt is None and self.answer_remote is not None and "/" in request.path_params["prompt"]:
            status, answer = await self.answer_remote(request.path_params["prompt"], await request.json())
            return JSONResponse(answer, status)
        if prompt is None or prompt["answer"] is not None:
            return JSONResponse({"error": "this prompt is gone"}, 404)
        body = await request.json()
        # RULE: an answer that does not fit the prompt is refused
        reason = self._fits(prompt, body)
        if reason:
            return JSONResponse({"error": reason}, 409)
        prompt["answer"] = {k: body[k] for k in ("answer", "decision", "reason") if k in body}
        prompt["done"].set()
        caller = "page" if self._cookie(request) else "bearer"
        log.info(
            "prompt %s for %s answered %s from %s",
            prompt["id"],
            prompt["session"],
            json.dumps(prompt["answer"], ensure_ascii=False),
            caller,
        )
        HUB.publish("prompt_closed", {"id": prompt["id"], "how": "phone"})
        return JSONResponse({"id": prompt["id"], "answered": True})

    async def wait_prompt(self, request: Request) -> Response:
        if not await self._bearer(request):
            return JSONResponse({"error": "sign in first"}, 401)
        prompt = self.prompts.get(request.path_params["prompt"])
        if prompt is None:
            return JSONResponse({"error": "this prompt is gone"}, 404)
        seconds = min(float(request.query_params.get("seconds", "30") or 30), 60.0)
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(prompt["done"].wait(), seconds)
        if prompt["answer"] is None:
            return Response(status_code=204)
        self.prompts.pop(prompt["id"], None)
        return JSONResponse(prompt["answer"])

    async def close_prompt(self, request: Request) -> Response:
        if not await self._bearer(request):
            return JSONResponse({"error": "sign in first"}, 401)
        prompt = self.prompts.pop(request.path_params["prompt"], None)
        if prompt is not None:
            how = str((await request.json()).get("how", "screen"))
            log.info("prompt %s for %s left to the %s", prompt["id"], prompt["session"], how)
            HUB.publish("prompt_closed", {"id": prompt["id"], "how": how})
        return Response(status_code=204)

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
            yield f"event: hello\ndata: {json.dumps({'started_at': STARTED_AT})}\n\n"
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
