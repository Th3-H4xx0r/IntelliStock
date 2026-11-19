"""The MCP gate: listener-scoped service auth, the MCP principal and route policy.

One FastAPI app is served on two listeners (contract §2). This pure ASGI
middleware tells them apart by the port the request arrived on, never by a
header:

- On the public listener it is invisible. The scope, receive and send it was
  handed go to the app unchanged, so a human JWT caller sees exactly what an app
  without the gate would show. The one exception the contract mandates: a bearer
  equal to MCP_SERVICE_TOKEN gets the same 401 a bad JWT gets.
- On the MCP listener (MCP_API_PORT) it is the whole gate. The switch, the token,
  the raw socket peer and the acting user must all check out (anything else is a
  401), then the route's x-mcp policy decides (no policy or "never" is a 403).
  An admitted call runs with the principal in the ASGI scope, where
  ``main.get_current_user`` picks it up.

The scope keys below are set only here, in-process; no header or request field
can reach them. Every MCP-listener call except the service-level routes gets one
McpAudit row, refusals included. Store calls run in the threadpool.

Never import api.main here: ``python api/main.py`` runs it as __main__, so an
import would build a second app.
"""
from __future__ import annotations

import hmac
import ipaddress
import logging
import re
import time
from collections.abc import Callable, Mapping
from urllib.parse import parse_qsl

from fastapi import HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from starlette.routing import BaseRoute, Match
from starlette.types import ASGIApp, Message, Receive, Scope, Send

import auth_utils
from mcp_audit import summarize_args, write_audit
from mcp_policy import McpConfig, validate_policy

_log = logging.getLogger("uvicorn.error")

PRINCIPAL_SCOPE_KEY = "intellistock.mcp_principal"
SERVICE_SCOPE_KEY = "intellistock.mcp_service"
REPLAY_SCOPE_KEY = "intellistock.mcp_replay"

# On the MCP listener these need only the service auth (switch, token, peer),
# no acting user, and are not audited here (contract §4).
SERVICE_LEVEL_ROUTES = frozenset({
    ("GET", "/health"),
    ("GET", "/internal/mcp/openapi.json"),
    ("POST", "/internal/mcp/verify-password"),
    ("POST", "/internal/mcp/audit-event"),
})

DEFAULT_CLIENT = "jarvis"
CLIENT_RE = re.compile(r"^[A-Za-z0-9._-]{1,32}$")
_CORRELATION_ID_MAX = 128

UNAUTHENTICATED_DETAIL = "mcp: unauthenticated"
NOT_AVAILABLE_DETAIL = "mcp: operation not available"
# What get_current_user answers for a token it cannot decode: the service token
# on the public listener must look like any other bad bearer.
_BAD_JWT_DETAIL = "Invalid or expired token"


def principal_from_scope(scope: Mapping) -> dict | None:
    """A copy of the MCP principal the gate placed in the scope, or None."""
    value = scope.get(PRINCIPAL_SCOPE_KEY)
    if isinstance(value, Mapping) and value.get("via") == "mcp":
        return dict(value)
    return None


def match_route(app: object, scope: Scope) -> tuple[BaseRoute | None, dict]:
    """The first route that fully matches the request, and its path params."""
    for route in getattr(app, "routes", None) or ():
        match, child_scope = route.matches(scope)
        if match == Match.FULL:
            return route, dict(child_scope.get("path_params") or {})
    return None, {}


def route_policy(route: BaseRoute | None) -> dict | None:
    """The route's validated x-mcp object; None when it has none or it is malformed."""
    return validate_policy((getattr(route, "openapi_extra", None) or {}).get("x-mcp"))


def _header_values(scope: Scope, name: bytes) -> list[bytes]:
    return [value for key, value in scope.get("headers") or () if key.lower() == name]


def _peer(scope: Scope) -> str | None:
    client = scope.get("client")
    return str(client[0]) if client else None


def service_auth_failure(scope: Scope, cfg: McpConfig) -> str | None:
    """None when the request passes service auth, else the first failing check:
    "disabled", "no_token", "no_cidr", "peer" or "token"."""
    if not cfg.enabled:
        return "disabled"
    if cfg.service_token is None:
        return "no_token"
    if cfg.allowed_cidr is None:
        return "no_cidr"
    peer = _peer(scope)
    try:
        if peer is None or ipaddress.ip_address(peer) not in cfg.allowed_cidr:
            return "peer"
    except ValueError:
        return "peer"
    authorizations = _header_values(scope, b"authorization")
    expected = ("Bearer " + cfg.service_token).encode("utf-8")
    if len(authorizations) != 1 or not hmac.compare_digest(authorizations[0], expected):
        return "token"
    return None


def _bears_service_token(scope: Scope, token: str) -> bool:
    """True when any Authorization header is a bearer equal to the service token.

    Parsed the way HTTPBearer parses it (scheme case-insensitive), so no spelling
    of the token slips past on the public listener.
    """
    expected = token.encode("utf-8")
    for raw in _header_values(scope, b"authorization"):
        scheme, _, credentials = raw.partition(b" ")
        if scheme.lower() == b"bearer" and hmac.compare_digest(credentials.strip(), expected):
            return True
    return False


def _query(scope: Scope) -> dict:
    """The query string as a dict; a repeated key becomes a list of its values."""
    out: dict = {}
    for key, value in parse_qsl(scope.get("query_string", b"").decode("latin-1"), keep_blank_values=True):
        if key not in out:
            out[key] = value
        elif isinstance(out[key], list):
            out[key].append(value)
        else:
            out[key] = [out[key], value]
    return out


def _correlation_id(scope: Scope) -> str | None:
    values = _header_values(scope, b"x-mcp-correlation-id")
    return values[0].decode("latin-1")[:_CORRELATION_ID_MAX] if values else None


def _client_header(scope: Scope) -> tuple[bool, str | None]:
    """(valid, value) for X-MCP-Client: absent is valid (None); more than one, or
    one that breaks CLIENT_RE, is not."""
    values = _header_values(scope, b"x-mcp-client")
    if not values:
        return True, None
    if len(values) > 1:
        return False, None
    value = values[0].decode("latin-1")
    return (True, value) if CLIENT_RE.fullmatch(value) else (False, None)


async def _acting_user(scope: Scope) -> dict | None:
    """The Users row named by the one X-MCP-Acting-User header, or None."""
    values = _header_values(scope, b"x-mcp-acting-user")
    if len(values) != 1:
        return None
    user_id = values[0].decode("latin-1")
    if not user_id:
        return None
    try:
        user = await run_in_threadpool(auth_utils.get_user_by_id, None, user_id)
    except Exception as exc:
        # The type only: a message can quote the request.
        _log.warning("mcp: acting-user lookup failed (%s); refusing", type(exc).__name__)
        return None
    return user if isinstance(user, dict) else None


async def _respond(scope: Scope, receive: Receive, send: Send, status: int, detail: str,
                   headers: dict | None = None) -> None:
    await JSONResponse({"detail": detail}, status_code=status, headers=headers)(scope, receive, send)


async def _audit(scope: Scope, started: float, *, outcome: str, status: int,
                 principal: dict | None = None, client: str | None = None,
                 operation_id: str | None = None) -> None:
    row = {
        "outcome": outcome,
        "status": status,
        "latency_ms": int((time.perf_counter() - started) * 1000),
        "method": scope.get("method"),
        "path": scope.get("path"),
        "peer": _peer(scope),
        "client": principal["client"] if principal else client,
        "user_id": principal["id"] if principal else None,
        "username": principal["username"] if principal else None,
        "operation_id": operation_id,
        "correlation_id": _correlation_id(scope),
        "args_summary": summarize_args(_query(scope), None),
    }
    await run_in_threadpool(write_audit, row)


class McpGateMiddleware:
    """Pass the public listener through untouched; gate the MCP listener."""

    def __init__(self, app: ASGIApp, *,
                 config_loader: Callable[[], McpConfig] = McpConfig.from_env) -> None:
        self.app = app
        self.config_loader = config_loader

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        cfg = self.config_loader()
        server = scope.get("server")
        if not server or server[1] != cfg.port:
            # Public listener: the service token is refused like a bad JWT;
            # everything else reaches the app as the very objects we were given.
            if cfg.service_token and _bears_service_token(scope, cfg.service_token):
                await _respond(scope, receive, send, 401, _BAD_JWT_DETAIL)
                return
            await self.app(scope, receive, send)
            return
        await self._gate(scope, receive, send, cfg)

    async def _gate(self, scope: Scope, receive: Receive, send: Send, cfg: McpConfig) -> None:
        started = time.perf_counter()
        failure = service_auth_failure(scope, cfg)
        if failure is not None:
            await self._unauthenticated(scope, receive, send, started, failure)
            return
        if (scope.get("method"), scope.get("path")) in SERVICE_LEVEL_ROUTES:
            scope[SERVICE_SCOPE_KEY] = True
            await self.app(scope, receive, send)
            return

        client_ok, client = _client_header(scope)
        if not client_ok:
            await self._unauthenticated(scope, receive, send, started, "client")
            return
        user = await _acting_user(scope)
        if user is None:
            await self._unauthenticated(scope, receive, send, started, "acting_user", client=client)
            return
        principal = {"id": user.get("id"), "username": user.get("username"),
                     "via": "mcp", "client": client or DEFAULT_CLIENT}

        route, _path_params = match_route(scope.get("app"), scope)
        policy = route_policy(route)
        operation_id = getattr(route, "operation_id", None)
        if policy is None or policy["class"] == "never":
            await self._forbidden(scope, receive, send, started, principal, operation_id)
            return
        if policy["class"] != "read":
            # TEMPORARY: Task 11 replaces this with the write/money/irreversible path.
            await self._forbidden(scope, receive, send, started, principal, operation_id)
            return

        scope[PRINCIPAL_SCOPE_KEY] = principal
        status: int | None = None

        async def capturing_send(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, capturing_send)
        except Exception:
            await _audit(scope, started, outcome="error", status=500, principal=principal,
                         operation_id=operation_id)
            raise
        final = 500 if status is None else status
        await _audit(scope, started, outcome="ok" if final < 400 else "error", status=final,
                     principal=principal, operation_id=operation_id)

    async def _unauthenticated(self, scope: Scope, receive: Receive, send: Send, started: float,
                               reason: str, *, client: str | None = None) -> None:
        _log.warning("mcp: unauthenticated request refused (%s) from %s", reason, _peer(scope))
        await _respond(scope, receive, send, 401, UNAUTHENTICATED_DETAIL,
                       headers={"WWW-Authenticate": "Bearer"})
        await _audit(scope, started, outcome="unauthenticated", status=401, client=client)

    async def _forbidden(self, scope: Scope, receive: Receive, send: Send, started: float,
                         principal: dict, operation_id: str | None) -> None:
        await _respond(scope, receive, send, 403, NOT_AVAILABLE_DETAIL)
        await _audit(scope, started, outcome="forbidden", status=403, principal=principal,
                     operation_id=operation_id)


def require_mcp_service(request: Request) -> None:
    """FastAPI dependency for the service-level routes: 401 unless the gate passed
    the request's service auth on the MCP listener."""
    if not request.scope.get(SERVICE_SCOPE_KEY):
        raise HTTPException(status_code=401, detail=UNAUTHENTICATED_DETAIL)
