"""The assembled service: dependencies, the Starlette app, its middleware, lifespan and housekeeping.

build_deps wires one of everything on one AuthDB and one ApiClient. build_app
asks the SDK for its Streamable HTTP app (stateless, JSON responses, /mcp
behind the SDK's bearer middleware with McpTokenVerifier, and the RFC 9728
protected-resource route) and mounts the authorization server, the sign-in and
enrollment pages, their static assets and /healthz beside it. Two middlewares
go outside everything:

- HostGuardMiddleware answers 421 to a Host header that is not one of
  Settings.allowed_hosts, on every path except /healthz (the container's own
  health check calls 127.0.0.1). Each distinct rejected Host is logged once,
  with the escape hatch, because tailscale serve can forward a Host that
  differs from MCP_PUBLIC_BASE and every request would otherwise fail silently.
- DisabledMiddleware answers 503 on /mcp, /authorize, /token, /login* and
  /enroll* while the admin CLI's kill switch is set.

The lifespan enters the SDK's own (its session manager) and then runs the
schema watcher, the audit flush loop and housekeeping every 60 s beside the
server; on shutdown it cancels them and closes the API client.

Sign-in serialises attempts with in-process asyncio locks (login.py), so the
server must run as one process with one event loop: uvicorn with one worker.

build_unconfigured_app is what the container serves when the settings are
incomplete: /healthz, and 503 everywhere else, so a stack merged before its
configuration exists idles instead of crash-looping.
"""
import contextlib
import logging
import time
from dataclasses import dataclass
from typing import AsyncIterator, Callable, Optional, Sequence

import anyio
import httpx
from mcp.server.auth.settings import AuthSettings
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.datastructures import Headers
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route
from starlette.types import ASGIApp, Receive, Scope, Send

from mcp_server.api_client import ApiClient
from mcp_server.audit import AuditSink
from mcp_server.authdb import AuthDB
from mcp_server.config import SCOPE, Settings
from mcp_server.enroll import enroll_routes
from mcp_server.login import login_routes
from mcp_server.oauth_provider import DISABLED_FLAG, IntelliStockOAuthProvider, build_auth_routes
from mcp_server.pages import WebDeps, static_routes
from mcp_server.passkeys import PasskeyService
from mcp_server.schema_watcher import SchemaWatcher
from mcp_server.sessions import ReuseEvent, SessionService
from mcp_server.throttle import LoginThrottle
from mcp_server.tokens import AccessTokenIssuer, McpTokenVerifier, SigningKeyring
from mcp_server.tools import ToolRegistry, make_server
from mcp_server.totp import TotpService

MCP_PATH = "/mcp"
HEALTHZ_PATH = "/healthz"
MAX_MCP_BODY_BYTES = 1_048_576
HOUSEKEEPING_INTERVAL_S = 60.0
ENROLLMENT_RETENTION_S = 86_400
MAX_LOGGED_HOSTS = 256  # distinct rejected Host values logged; a scanner could send endless new ones
# The kill switch covers every path that signs in, issues a token or runs a tool.
DISABLED_PATHS = (MCP_PATH, "/authorize", "/token", "/login", "/enroll")

logger = logging.getLogger(__name__)


@dataclass
class Deps:
    settings: Settings
    db: AuthDB
    keyring: SigningKeyring
    issuer: AccessTokenIssuer
    sessions: SessionService
    verifier: McpTokenVerifier
    throttle: LoginThrottle
    totp: TotpService
    passkeys: PasskeyService
    audit: AuditSink
    api: ApiClient
    registry: ToolRegistry
    watcher: SchemaWatcher
    provider: IntelliStockOAuthProvider
    clock: Callable[[], float]


def build_deps(settings: Settings, *, clock: Callable[[], float] = time.time,
               api_transport: Optional[httpx.AsyncBaseTransport] = None) -> Deps:
    """One of everything, on one auth store (the provider and its sessions must share it) and one API client."""
    db = AuthDB(settings.db_path)
    api = ApiClient(settings.api_base, settings.service_token, timeout_s=settings.api_timeout_s,
                    transport=api_transport)
    audit = AuditSink(db, api, clock=clock)

    def on_reuse(event: ReuseEvent) -> None:
        row = db.query_one("SELECT username FROM users WHERE user_id = ?", (event.user_id,))
        audit.record("refresh_reuse_revoked", session_id=event.family_id, user_id=event.user_id,
                     username=None if row is None else row["username"], client_id=event.client_id)

    def on_lockout(scope: str, username: Optional[str], seconds: int) -> None:
        audit.record("lockout", scope=scope, username=username, seconds=seconds)

    keyring = SigningKeyring(db, settings.master_key, clock=clock)
    issuer = AccessTokenIssuer(keyring, settings, clock=clock)
    sessions = SessionService(db, issuer, settings.master_key, clock=clock, on_reuse=on_reuse)
    verifier = McpTokenVerifier(keyring, settings, clock=clock, session_active=sessions.is_active,
                                disabled=lambda: db.get_flag(DISABLED_FLAG))
    registry = ToolRegistry(api)
    return Deps(
        settings=settings,
        db=db,
        keyring=keyring,
        issuer=issuer,
        sessions=sessions,
        verifier=verifier,
        throttle=LoginThrottle(db, clock=clock, on_lockout=on_lockout),
        totp=TotpService(db, settings.master_key, clock=clock),
        passkeys=PasskeyService(db, rp_id=settings.rp_id, origin=settings.public_origin, clock=clock),
        audit=audit,
        api=api,
        registry=registry,
        watcher=SchemaWatcher(api, registry.load, poll_s=settings.schema_poll_s),
        provider=IntelliStockOAuthProvider(settings, db, sessions, verifier, clock=clock),
        clock=clock,
    )


async def healthz(request: Request) -> Response:
    return JSONResponse({"ok": True})


class HostGuardMiddleware:
    """421 for a Host header outside allowed_hosts, on every path but /healthz; each distinct Host logged once."""

    def __init__(self, app: ASGIApp, *, allowed_hosts: Sequence[str]) -> None:
        self.app = app
        self._allowed = frozenset(allowed_hosts)
        self._allowed_text = ", ".join(allowed_hosts)
        self._logged: set = set()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["path"] == HEALTHZ_PATH:
            await self.app(scope, receive, send)
            return
        host = Headers(scope=scope).get("host", "")
        if host in self._allowed:
            await self.app(scope, receive, send)
            return
        if host not in self._logged and len(self._logged) < MAX_LOGGED_HOSTS:
            self._logged.add(host)
            logger.warning("Rejected Host header %r; allowed: %s. Set MCP_EXTRA_ALLOWED_HOSTS if tailscale serve "
                           "rewrites Host.", host[:200], self._allowed_text)
        await JSONResponse({"error": "misdirected request"}, status_code=421)(scope, receive, send)


def _disabled_path(path: str) -> bool:
    return any(path == prefix or path.startswith(prefix + "/") for prefix in DISABLED_PATHS)


class DisabledMiddleware:
    """503 {"error": "disabled"} on the sign-in, token and /mcp paths while the kill switch is set."""

    def __init__(self, app: ASGIApp, *, disabled: Callable[[], bool]) -> None:
        self.app = app
        self._disabled = disabled

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and _disabled_path(scope["path"]) and self._disabled():
            await JSONResponse({"error": "disabled"}, status_code=503)(scope, receive, send)
            return
        await self.app(scope, receive, send)


def _purge_enrollments(deps: Deps) -> None:
    """Delete enrollments, finished or not, whose link ran out more than a day ago."""
    deps.db.query("DELETE FROM enrollments WHERE expires_at < ?", (deps.clock() - ENROLLMENT_RETENTION_S,))


async def housekeeping(deps: Deps) -> None:
    """One pass: sessions, login transactions and codes, throttle rows, old enrollments, old audit rows.

    A step that fails is logged by its exception type (a message could quote
    stored data) and the pass goes on to the next.
    """
    steps = (
        ("sessions", deps.sessions.purge),
        ("login transactions", deps.provider.purge),
        ("throttle", deps.throttle.purge),
        ("enrollments", lambda: _purge_enrollments(deps)),
        ("audit events", deps.audit.purge),
    )
    for name, step in steps:
        try:
            step()
        except Exception as exc:
            logger.warning("Housekeeping of %s failed (%s); it is retried on the next pass.", name,
                           type(exc).__name__)


async def _housekeeping_loop(deps: Deps) -> None:
    while True:
        await housekeeping(deps)
        await anyio.sleep(HOUSEKEEPING_INTERVAL_S)


def _web_deps(deps: Deps) -> WebDeps:
    return WebDeps(settings=deps.settings, db=deps.db, provider=deps.provider, api=deps.api, throttle=deps.throttle,
                   totp=deps.totp, passkeys=deps.passkeys, audit=deps.audit, sessions=deps.sessions,
                   clock=deps.clock)


def build_app(deps: Deps) -> Starlette:
    """The whole service on deps; see the module docstring."""
    settings = deps.settings
    web = _web_deps(deps)
    app = make_server(deps.registry).streamable_http_app(
        streamable_http_path=MCP_PATH,
        json_response=True,
        stateless_http=True,
        max_request_body_size=MAX_MCP_BODY_BYTES,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=list(settings.allowed_hosts),
            allowed_origins=[settings.public_origin],
        ),
        auth=AuthSettings(issuer_url=settings.issuer, resource_server_url=settings.resource_url,
                          required_scopes=[SCOPE]),
        token_verifier=deps.verifier,
        custom_starlette_routes=(build_auth_routes(deps.provider, settings) + login_routes(web) + enroll_routes(web)
                                 + static_routes() + [Route(HEALTHZ_PATH, healthz, methods=["GET"])]),
    )
    # add_middleware puts each one outside the last: the Host check runs first, before the kill switch.
    app.add_middleware(DisabledMiddleware, disabled=lambda: deps.db.get_flag(DISABLED_FLAG))
    app.add_middleware(HostGuardMiddleware, allowed_hosts=settings.allowed_hosts)

    sdk_lifespan = app.router.lifespan_context

    @contextlib.asynccontextmanager
    async def lifespan(scope_app: Starlette) -> AsyncIterator[object]:
        try:
            async with sdk_lifespan(scope_app) as state:
                async with anyio.create_task_group() as tasks:
                    tasks.start_soon(deps.watcher.run)
                    tasks.start_soon(deps.audit.flush_loop)
                    tasks.start_soon(_housekeeping_loop, deps)
                    try:
                        yield state
                    finally:
                        tasks.cancel_scope.cancel()
        finally:
            await deps.api.aclose()

    app.router.lifespan_context = lifespan
    return app


class _NotConfiguredMiddleware:
    """503 {"error": "not configured"} for every HTTP path but /healthz."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["path"] != HEALTHZ_PATH:
            await JSONResponse({"error": "not configured"}, status_code=503)(scope, receive, send)
            return
        await self.app(scope, receive, send)


def build_unconfigured_app(errors: list[str]) -> Starlette:
    """/healthz and 503 elsewhere; at startup, one log line per settings error (they never quote a value)."""
    problems = list(errors)

    @contextlib.asynccontextmanager
    async def lifespan(scope_app: Starlette) -> AsyncIterator[None]:
        for problem in problems:
            logger.error("Invalid MCP settings: %s", problem)
        logger.error("The MCP server is not configured: it answers /healthz and 503 everywhere else until the "
                     "settings above are fixed and the container is restarted.")
        yield

    return Starlette(routes=[Route(HEALTHZ_PATH, healthz, methods=["GET"])],
                     middleware=[Middleware(_NotConfiguredMiddleware)], lifespan=lifespan)
