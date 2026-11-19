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
  The body is then read whole (413 past MCP_MAX_BODY_BYTES; 415 unless it is
  JSON sent as application/json, and also for JSON the gate cannot hash or
  nests deeper than MAX_JSON_DEPTH), so the money decision, a held approval and
  the route all see the same bytes. A money call on a live, unknown or
  protected target, and every irreversible call, is held: a pending approval,
  a push and a 202, and the route does not run. Anything else runs with the
  principal in the ASGI scope, where ``main.get_current_user`` picks it up.

One more branch comes before the listener test: a scope carrying
REPLAY_SCOPE_KEY is an approved call that the decision route replays in-process
(api.mcp_replay). It runs only if it wins the approval's one execution
(mcp_approvals.claim_execution) and the stored row names this method and path
for the approving user; it then runs as that user's MCP principal, with no
idempotency and no caps, and is audited as "executed_approved". Anything else
is a 403 audited as "replay_refused".

The scope keys below are set only here, in-process; no header or request field
can reach them. Every MCP-listener call except the service-level routes gets one
McpAudit row, refusals included, and so does every replay. Store calls run in
the threadpool.

Never import api.main here: ``python api/main.py`` runs it as __main__, so an
import would build a second app.
"""
from __future__ import annotations

import hmac
import ipaddress
import json
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
import mcp_approvals
from mcp_audit import summarize_args, write_audit
from mcp_policy import (
    McpConfig, approval_reason, canonical_request_sha256, resolve_money_state, validate_policy,
)

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

# Contract §2. Matched with fullmatch: `$` alone would accept a trailing newline.
IDEMPOTENCY_KEY_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")

UNAUTHENTICATED_DETAIL = "mcp: unauthenticated"
NOT_AVAILABLE_DETAIL = "mcp: operation not available"
NOT_EXECUTABLE_DETAIL = "mcp: approval is not executable"
TOO_LARGE_DETAIL = "mcp: request body too large"
JSON_REQUIRED_DETAIL = "mcp: JSON body required"
UNPROCESSABLE_DETAIL = "mcp: request body must be JSON the gate can process"
UNPROCESSABLE_SUMMARY = "[unprocessable]"
# Containers nested deeper than this are refused, so nothing downstream (the
# hash, the redaction, the route) can run out of recursion on a body.
MAX_JSON_DEPTH = 64
# Audited for a client that hung up before its body was complete (nginx's code).
CLIENT_CLOSED_STATUS = 499
# What get_current_user answers for a token it cannot decode: the service token
# on the public listener must look like any other bad bearer.
_BAD_JWT_DETAIL = "Invalid or expired token"


def principal_from_scope(scope: Mapping) -> dict | None:
    """A copy of the MCP principal the gate placed in the scope, or None."""
    value = scope.get(PRINCIPAL_SCOPE_KEY)
    if isinstance(value, Mapping) and value.get("via") == "mcp":
        return dict(value)
    return None


def _replay_principal(value: object) -> dict | None:
    """The MCP principal a replay marker carries, as the four principal keys; None
    unless it is a mapping with via "mcp" and a user id."""
    if not isinstance(value, Mapping) or value.get("via") != "mcp" or not value.get("id"):
        return None
    return {"id": value["id"], "username": value.get("username"), "via": "mcp",
            "client": value.get("client")}


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


def _idempotency_key(scope: Scope) -> str | None:
    """The one Idempotency-Key header when it is well formed, else None."""
    values = _header_values(scope, b"idempotency-key")
    if len(values) != 1:
        return None
    key = values[0].decode("latin-1")
    return key if IDEMPOTENCY_KEY_RE.fullmatch(key) else None


class _BodyTooLarge(Exception):
    """The request body is over cfg.max_body_bytes, declared or received."""


class _ClientGone(Exception):
    """The client disconnected before its body was complete."""


class _NotJson(Exception):
    """A non-empty body that is not JSON sent as application/json."""


class _Unprocessable(Exception):
    """JSON the gate cannot handle: nested past MAX_JSON_DEPTH, or unhashable."""


def _declared_length(scope: Scope) -> int | None:
    values = _header_values(scope, b"content-length")
    if len(values) != 1 or not values[0].strip().isdigit():
        return None
    return int(values[0])


async def _read_body(scope: Scope, receive: Receive, limit: int) -> bytes:
    """The whole request body. _BodyTooLarge as soon as the declared length or
    the bytes received pass `limit`; _ClientGone on a disconnect mid-body."""
    declared = _declared_length(scope)
    if declared is not None and declared > limit:
        raise _BodyTooLarge()
    chunks: list[bytes] = []
    size = 0
    while True:
        message = await receive()
        if message["type"] == "http.disconnect":
            raise _ClientGone()
        chunk = message.get("body", b"")
        size += len(chunk)
        if size > limit:
            raise _BodyTooLarge()
        chunks.append(chunk)
        if not message.get("more_body", False):
            return b"".join(chunks)


def _reject_constant(name: str) -> object:
    raise ValueError(f"{name} is not JSON")


def _nests_deeper_than(value: object, limit: int) -> bool:
    """True when dicts and lists nest more than `limit` levels; iterative, so it
    cannot run out of recursion itself."""
    stack = [(value, 1)] if isinstance(value, (dict, list)) else []
    while stack:
        node, depth = stack.pop()
        if depth > limit:
            return True
        children = node.values() if isinstance(node, dict) else node
        stack.extend((child, depth + 1) for child in children if isinstance(child, (dict, list)))
    return False


def _json_body(scope: Scope, body: bytes) -> object:
    """The parsed body, or None for an empty one.

    _NotJson unless the media type is application/json and the bytes parse.
    NaN and Infinity are refused although Python reads them: they are not JSON,
    so a held call carrying one could never be stored or re-hashed by the app.
    _Unprocessable when containers nest past MAX_JSON_DEPTH, including nesting
    so deep that the parser itself gives up.
    """
    if not body:
        return None
    values = _header_values(scope, b"content-type")
    media_type = values[0].split(b";", 1)[0].strip().lower() if values else b""
    if media_type != b"application/json":
        raise _NotJson()
    try:
        parsed = json.loads(body, parse_constant=_reject_constant)
    except RecursionError as exc:
        raise _Unprocessable() from exc
    except ValueError as exc:
        raise _NotJson() from exc
    if _nests_deeper_than(parsed, MAX_JSON_DEPTH):
        raise _Unprocessable()
    return parsed


def _buffered_receive(body: bytes, receive: Receive) -> Receive:
    """A receive that yields the buffered body as one http.request, then defers
    to the real receive (for the disconnect)."""
    delivered = False

    async def buffered() -> Message:
        nonlocal delivered
        if not delivered:
            delivered = True
            return {"type": "http.request", "body": body, "more_body": False}
        return await receive()

    return buffered


class _ResponseCapture:
    """A send that forwards every message unchanged and notes the status and,
    when keep_body, the body chunks."""

    def __init__(self, send: Send, *, keep_body: bool) -> None:
        self._send = send
        self._keep_body = keep_body
        self.status: int | None = None
        self.body_chunks: list[bytes] = []

    async def send(self, message: Message) -> None:
        if message["type"] == "http.response.start":
            self.status = message["status"]
        elif message["type"] == "http.response.body" and self._keep_body:
            self.body_chunks.append(message.get("body", b""))
        await self._send(message)


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
                 operation_id: str | None = None, **fields: object) -> None:
    """Write the call's McpAudit row. `fields` adds columns or overrides the
    defaults; args_summary defaults to the query alone."""
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
        **fields,
    }
    if "args_summary" not in row:
        row["args_summary"] = summarize_args(_query(scope), None)
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
        if REPLAY_SCOPE_KEY in scope:
            # Only api.mcp_replay sets this key, in-process; it has no listener.
            await self._replay(scope, receive, send)
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

        route, path_params = match_route(scope.get("app"), scope)
        policy = route_policy(route)
        operation_id = getattr(route, "operation_id", None)
        if policy is None or policy["class"] == "never":
            await self._refuse(scope, receive, send, started, principal, operation_id,
                               status=403, detail=NOT_AVAILABLE_DETAIL, outcome="forbidden")
            return

        # The whole body before anything decides: the money state, a held
        # approval and the route all see these bytes.
        try:
            body = await _read_body(scope, receive, cfg.max_body_bytes)
        except _BodyTooLarge:
            await self._refuse(scope, receive, send, started, principal, operation_id,
                               status=413, detail=TOO_LARGE_DETAIL, outcome="too_large")
            return
        except _ClientGone:
            await _audit(scope, started, outcome="error", status=CLIENT_CLOSED_STATUS,
                         principal=principal, operation_id=operation_id)
            return
        # Parse, hash and summarize under one guard. JSON that parses but cannot
        # be hashed (a lone surrogate escape cannot be UTF-8 encoded) is refused
        # like JSON nested too deep, and still gets its one audit row.
        try:
            body_obj = _json_body(scope, body)
            query = _query(scope)
            args_sha256 = canonical_request_sha256(scope["method"], scope["path"], query, body_obj)
            args_summary = summarize_args(query, body_obj)
        except _NotJson:
            await self._refuse(scope, receive, send, started, principal, operation_id,
                               status=415, detail=JSON_REQUIRED_DETAIL, outcome="unsupported_media")
            return
        except (_Unprocessable, ValueError, RecursionError):
            await self._refuse(scope, receive, send, started, principal, operation_id,
                               status=415, detail=UNPROCESSABLE_DETAIL, outcome="rejected",
                               args_summary=UNPROCESSABLE_SUMMARY)
            return
        idempotency_key = _idempotency_key(scope)
        audit_fields: dict = {"args_sha256": args_sha256, "args_summary": args_summary,
                              "idempotency_key": idempotency_key}

        # [Task 14: the Idempotency-Key check, lookup and claim go here.]
        # [Task 15: the rate caps, the daily cost cap and the backtest slot go here.]

        money_state = None
        if policy["class"] == "money":
            money_state = await run_in_threadpool(resolve_money_state, policy, path_params,
                                                  body_obj, cfg)
        reason = approval_reason(policy, money_state)
        audit_fields["money_state"] = reason or money_state

        response = _ResponseCapture(send, keep_body=policy["class"] != "read")
        approval_id: str | None = None
        try:
            if reason is not None:
                # [Task 15: the daily approvals cap goes here.]
                row = await _create_held_approval(
                    scope, cfg, principal=principal, operation_id=operation_id,
                    path_params=path_params, query=query, body=body_obj, reason=reason,
                    idempotency_key=idempotency_key)
                approval_id = row["id"]
                pending = {"id": row["id"], "summary": row["summary"], "money_state": reason,
                           "expires_at": row["expires_at"]}
                await JSONResponse({"pending_approval": pending}, status_code=202)(
                    scope, receive, response.send)
            else:
                scope[PRINCIPAL_SCOPE_KEY] = principal
                await self.app(scope, _buffered_receive(body, receive), response.send)
        except Exception:
            # [Task 14: release the idempotency record here.]
            await _audit(scope, started, outcome="error", status=500, principal=principal,
                         operation_id=operation_id, approval_id=approval_id, **audit_fields)
            raise
        # [Task 14: finalize the idempotency record with the captured response here.]
        status = 500 if response.status is None else response.status
        if approval_id is not None:
            outcome = "approval_required"
        else:
            outcome = "ok" if status < 400 else "error"
        await _audit(scope, started, outcome=outcome, status=status, principal=principal,
                     operation_id=operation_id, approval_id=approval_id, **audit_fields)

    async def _replay(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Run an approved call the decision route replays in-process, exactly once.

        The approval's execution is started first (claim_execution), so a second
        replay of the same approval, or one of a row that is not approved and
        claimed, is refused; so is a row of another user or one that names
        another method or path, and a route that is gone or class "never". No
        idempotency and no caps: the call was counted when it was held.
        """
        started = time.perf_counter()
        marker = scope.get(REPLAY_SCOPE_KEY)
        marker = marker if isinstance(marker, Mapping) else {}
        approval_id = marker.get("approval_id")
        approval_id = approval_id if mcp_approvals.is_approval_id(approval_id) else None
        principal = _replay_principal(marker.get("principal"))
        row = None
        if principal is not None and approval_id is not None:
            row = await run_in_threadpool(mcp_approvals.claim_execution, approval_id)
        if (row is None or row.get("user_id") != principal["id"]
                or row.get("method") != scope.get("method") or row.get("path") != scope.get("path")):
            await self._refuse_replay(scope, receive, send, started, principal, approval_id, None,
                                      NOT_EXECUTABLE_DETAIL)
            return

        route, _ = match_route(scope.get("app"), scope)
        policy = route_policy(route)
        operation_id = getattr(route, "operation_id", None)
        if policy is None or policy["class"] == "never":
            await self._refuse_replay(scope, receive, send, started, principal, approval_id, operation_id,
                                      NOT_AVAILABLE_DETAIL)
            return

        audit_fields = {"approval_id": approval_id, "money_state": row.get("money_state"),
                        "args_sha256": row.get("body_sha256"),
                        "args_summary": summarize_args(row.get("query") or {}, row.get("body"))}
        scope[PRINCIPAL_SCOPE_KEY] = principal
        response = _ResponseCapture(send, keep_body=False)

        async def audit() -> None:
            await _audit(scope, started, outcome="executed_approved",
                         status=500 if response.status is None else response.status,
                         principal=principal, operation_id=operation_id, **audit_fields)

        try:
            await self.app(scope, receive, response.send)
        except Exception:
            await audit()
            raise
        await audit()

    async def _refuse_replay(self, scope: Scope, receive: Receive, send: Send, started: float,
                             principal: dict | None, approval_id: str | None, operation_id: str | None,
                             detail: str) -> None:
        await _respond(scope, receive, send, 403, detail)
        await _audit(scope, started, outcome="replay_refused", status=403, principal=principal,
                     operation_id=operation_id, approval_id=approval_id)

    async def _unauthenticated(self, scope: Scope, receive: Receive, send: Send, started: float,
                               reason: str, *, client: str | None = None) -> None:
        _log.warning("mcp: unauthenticated request refused (%s) from %s", reason, _peer(scope))
        await _respond(scope, receive, send, 401, UNAUTHENTICATED_DETAIL,
                       headers={"WWW-Authenticate": "Bearer"})
        await _audit(scope, started, outcome="unauthenticated", status=401, client=client)

    async def _refuse(self, scope: Scope, receive: Receive, send: Send, started: float,
                      principal: dict, operation_id: str | None, *, status: int, detail: str,
                      outcome: str, **audit_fields: object) -> None:
        await _respond(scope, receive, send, status, detail)
        await _audit(scope, started, outcome=outcome, status=status, principal=principal,
                     operation_id=operation_id, **audit_fields)


async def _create_held_approval(scope: Scope, cfg: McpConfig, *, principal: dict,
                                operation_id: str | None, path_params: dict, query: dict,
                                body: object, reason: str, idempotency_key: str | None) -> dict:
    """Store the call as a pending approval and start its push; the new row.

    A failed insert raises, so the call fails closed. The push is best-effort:
    an approval that exists is answered with its 202 whether or not it went out.
    """
    method, path = scope["method"], scope["path"]
    summary = mcp_approvals.build_summary(operation_id, method, path, path_params, body, reason)
    row = await run_in_threadpool(
        mcp_approvals.create_approval, principal=principal, operation_id=operation_id,
        method=method, path=path, query=query, body=body, money_state=reason, summary=summary,
        idempotency_key=idempotency_key, correlation_id=_correlation_id(scope), cfg=cfg)
    try:
        mcp_approvals.notify_approval_async(row)
    except Exception as exc:
        # The type only: a message can quote the summary.
        _log.warning("mcp: approval push not started (%s)", type(exc).__name__)
    return row


def require_mcp_service(request: Request) -> None:
    """FastAPI dependency for the service-level routes: 401 unless the gate passed
    the request's service auth on the MCP listener."""
    if not request.scope.get(SERVICE_SCOPE_KEY):
        raise HTTPException(status_code=401, detail=UNAUTHENTICATED_DETAIL)
