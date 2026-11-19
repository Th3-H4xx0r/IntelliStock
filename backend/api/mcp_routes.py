"""MCP approval routes (contract §5).

Built over the API's session dependency (``get_current_user`` in api/main.py),
the app_updates pattern, so this module never imports main: ``python
api/main.py`` runs main as __main__, and an import would build a second app.
The viewer is whatever that dependency resolves: a human JWT session on the
public listener, or the MCP principal the gate placed in the scope on :8012.

The two reads are MCP tools (class "read"). A human sees all of their own
approvals; an MCP principal sees only those its own client created. An unknown,
foreign or malformed id is a 404 either way. Handlers are plain ``def``, so
their store calls run in the threadpool.

The approval-device routes are class "never": only a human enrols, lists or
revokes a signing key. The gate already refuses them on :8012, and each handler
refuses an MCP principal again (defense in depth).

The decision route is class "never" too, and mcp_approvals.decide refuses an MCP
principal again. It is the one async handler: an approval is replayed through
the app in-process (api.mcp_replay) on the event loop, so its store calls go
through run_in_threadpool, and the replayed route runs as it always does.

The three /internal/mcp/* routes (contract §4) serve the MCP server itself. They
are class "never" and out of the schema; on :8012 the gate passes them with the
service auth alone (no acting user, no gate audit row), and require_mcp_service
answers 401 everywhere else, the public listener included, before the body is
validated. The OpenAPI route hands the MCP server the operations it may call,
with the schema hash /health also carries; verify-password checks a password
over the login limiter keyed (username, "mcp"); audit-event stores every auth
event and relays the security ones to Discord, since the MCP container has no
webhook of its own. Each writes its own McpAudit row, never with a password.
"""
from __future__ import annotations

import copy
import hashlib
import logging
import secrets
from collections.abc import Callable

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import auth_utils
import mcp_approvals
import notifications
from api.mcp_gate import CLIENT_RE, NOT_AVAILABLE_DETAIL, UNPROCESSABLE_SUMMARY, require_mcp_service
from api.mcp_replay import replay_approved_request
from mcp_audit import redact_secrets, summarize_args, write_audit
from mcp_policy import McpConfig, canonical_json, mcp_policy, validate_policy

_log = logging.getLogger("uvicorn.error")

LIST_LIMIT_MIN = 1
LIST_LIMIT_MAX = 200
NOT_FOUND_DETAIL = mcp_approvals.NOT_FOUND_DETAIL
DEVICE_NOT_FOUND_DETAIL = "device not found"

# -- internal routes (contract §4) ------------------------------------------------

AUTH_EVENTS = frozenset({"login_ok", "login_failed", "passkey_failed", "totp_failed", "lockout",
                         "refresh_reuse_revoked", "session_revoked", "enrolled", "disabled", "enabled"})
DISCORD_RELAYED_EVENTS = frozenset({"refresh_reuse_revoked", "lockout", "disabled"})
SCHEMA_HASH_KEY = "x-mcp-schema-hash"
INVALID_CREDENTIALS_DETAIL = "invalid credentials"
TOO_MANY_ATTEMPTS_DETAIL = "mcp: too many attempts"
# The limiter's "host": every MCP login arrives through the one MCP server.
MCP_LIMITER_HOST = "mcp"
AUTH_DETAIL_MAX = 2000
# Discord refuses a message over 2000 characters; the relay's summary is cut
# so the whole body stays under it.
RELAY_SUMMARY_MAX = 1900
# What the gate keeps of X-MCP-Correlation-Id.
_CORRELATION_ID_MAX = 128
_HTTP_METHODS = frozenset({"get", "put", "post", "delete", "options", "head", "patch", "trace"})
_SCHEMA_REF_PREFIX = "#/components/schemas/"
# bcrypt of a random string, made on first use: an unknown user is checked
# against it, so the time a check takes does not tell whether the user exists.
_DUMMY_BCRYPT_HASH: str | None = None


class VerifyPasswordBody(BaseModel):
    username: str
    password: str


def _schema_refs(node: object) -> set[str]:
    """The component schema names every $ref under node points at."""
    found: set[str] = set()
    stack = [node]
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            ref = item.get("$ref")
            if isinstance(ref, str) and ref.startswith(_SCHEMA_REF_PREFIX):
                found.add(ref[len(_SCHEMA_REF_PREFIX):])
            stack.extend(item.values())
        elif isinstance(item, list):
            stack.extend(item)
    return found


def _is_exposed(op: object) -> bool:
    """True for an operation whose x-mcp is valid and of a class other than "never"."""
    policy = validate_policy(op.get("x-mcp")) if isinstance(op, dict) else None
    return policy is not None and policy["class"] != "never"


def _exposed_paths(paths: dict) -> dict:
    """The path items that keep at least one exposed operation, with every other
    operation dropped; a path left with none is dropped whole."""
    kept: dict = {}
    for path, item in paths.items():
        exposed = {method for method, op in item.items() if method in _HTTP_METHODS and _is_exposed(op)}
        if exposed:
            kept[path] = {k: v for k, v in item.items() if k not in _HTTP_METHODS or k in exposed}
    return kept


def build_internal_openapi(app) -> dict:
    """The OpenAPI document the MCP server builds its tools from (contract §4).

    A deep copy of app.openapi() with only the operations an MCP caller may
    reach, each keeping its x-mcp object, and only the component schemas those
    operations reach, transitively. SCHEMA_HASH_KEY holds the sha256 of the
    canonical JSON of "paths". Cached on app.state.mcp_internal_openapi: the
    routes cannot change under a running app.
    """
    cached = getattr(app.state, "mcp_internal_openapi", None)
    if cached is not None:
        return cached
    doc = copy.deepcopy(app.openapi())
    doc["paths"] = _exposed_paths(doc.get("paths") or {})
    components = doc.get("components")
    if isinstance(components, dict) and isinstance(components.get("schemas"), dict):
        schemas = components["schemas"]
        reachable: set[str] = set()
        frontier = _schema_refs(doc["paths"])
        while frontier:
            name = frontier.pop()
            if name not in reachable and name in schemas:
                reachable.add(name)
                frontier |= _schema_refs(schemas[name])
        components["schemas"] = {name: schema for name, schema in schemas.items() if name in reachable}
    doc[SCHEMA_HASH_KEY] = hashlib.sha256(canonical_json(doc["paths"])).hexdigest()
    app.state.mcp_internal_openapi = doc
    return doc


def schema_hash(app) -> str:
    """The x-mcp-schema-hash of build_internal_openapi(app); /health carries it."""
    return build_internal_openapi(app)[SCHEMA_HASH_KEY]


def _dummy_bcrypt_hash() -> str:
    global _DUMMY_BCRYPT_HASH
    if _DUMMY_BCRYPT_HASH is None:
        _DUMMY_BCRYPT_HASH = auth_utils.hash_password(secrets.token_urlsafe(32))
    return _DUMMY_BCRYPT_HASH


def _internal_audit(request: Request, *, outcome: str, status: int, operation_id: str,
                    **fields: object) -> None:
    """One McpAudit row for an internal route; the client is X-MCP-Client when it
    is well formed. Never raises (write_audit)."""
    client = request.headers.get("x-mcp-client")
    correlation_id = request.headers.get("x-mcp-correlation-id")
    write_audit({
        "outcome": outcome,
        "status": status,
        "method": request.scope.get("method"),
        "path": request.scope.get("path"),
        "peer": request.client.host if request.client else None,
        "client": client if client and CLIENT_RE.fullmatch(client) else None,
        "operation_id": operation_id,
        "correlation_id": correlation_id[:_CORRELATION_ID_MAX] if correlation_id else None,
        **fields,
    })


def _auth_detail_summary(detail: dict) -> str:
    """The redacted detail as canonical JSON, cut to AUTH_DETAIL_MAX characters.
    A detail that cannot be encoded is summarized as "[unprocessable]", so the
    event itself is never lost."""
    try:
        text = canonical_json(redact_secrets(detail)).decode("utf-8")
    except (ValueError, RecursionError):
        return UNPROCESSABLE_SUMMARY
    return text[:AUTH_DETAIL_MAX]


def _relay_auth_event(event: str, summary: str) -> None:
    """Send a security event to Discord through the API's outbox; best-effort."""
    if len(summary) > RELAY_SUMMARY_MAX:
        summary = summary[:RELAY_SUMMARY_MAX] + "…"
    try:
        notifications.notify(category="other", instance_id="mcp", title=f"MCP security: {event}",
                             body=f"MCP SECURITY [{event}] {summary}", discord_channel="notifications")
    except Exception as exc:
        # The type only: a message can quote the event.
        _log.warning("mcp: auth-event relay failed (%s)", type(exc).__name__)


def _refuse_mcp_principal(current_user: dict) -> None:
    if current_user.get("via") == "mcp":
        raise HTTPException(status_code=403, detail=NOT_AVAILABLE_DETAIL)


def build_router(current_user_dependency: Callable) -> APIRouter:
    """The /mcp/approvals and /mcp/approval-devices routes, closed over the API's
    session dependency, and the /internal/mcp/* routes, which take the service
    auth instead."""
    router = APIRouter()
    authed = Depends(current_user_dependency)

    @router.get("/mcp/approvals", operation_id="list_pending_approvals",
                openapi_extra=mcp_policy("read", tool="list_pending_approvals"))
    def api_list_mcp_approvals(status: str = "pending", limit: int = 50, current_user: dict = authed):
        """List MCP approval requests (pending by default)."""
        if status not in mcp_approvals.LIST_STATUSES:
            raise HTTPException(status_code=400, detail="status must be pending or all")
        limit = min(max(limit, LIST_LIMIT_MIN), LIST_LIMIT_MAX)
        rows = mcp_approvals.list_visible(viewer=current_user, status=status, limit=limit,
                                          cfg=McpConfig.from_env())
        return {"approvals": [mcp_approvals.approval_public(row) for row in rows]}

    @router.get("/mcp/approvals/{approval_id}", operation_id="get_approval",
                openapi_extra=mcp_policy("read", tool="get_approval"))
    def api_get_mcp_approval(approval_id: str, current_user: dict = authed):
        """One MCP approval request and, once executed, its result."""
        row = mcp_approvals.get_visible(approval_id, viewer=current_user, cfg=McpConfig.from_env())
        if row is None:
            raise HTTPException(status_code=404, detail=NOT_FOUND_DETAIL)
        return mcp_approvals.approval_public(row)

    @router.post("/mcp/approvals/{approval_id}/decision", operation_id="decide_mcp_approval",
                 openapi_extra=mcp_policy("never"))
    async def api_mcp_approval_decision(approval_id: str, request: Request, payload: dict = Body(...),
                                        current_user: dict = authed):
        """Approve or deny a held MCP call with an approval device's signature; an approved call runs once."""
        cfg = McpConfig.from_env()
        try:
            row = await run_in_threadpool(mcp_approvals.decide, approval_id, payload, user=current_user, cfg=cfg)
        except mcp_approvals.DecisionError as exc:
            raise HTTPException(status_code=exc.status, detail=exc.detail) from None
        if row["status"] == "approved":
            principal = {"id": current_user["id"], "username": current_user.get("username"), "via": "mcp",
                         "client": row["client"]}
            result = await replay_approved_request(request.app, row, principal)
            recorded = await run_in_threadpool(mcp_approvals.record_result, row["id"],
                                               status_code=result.status, result=result.body)
            if recorded is None:
                # A read settled the execution first (stale): answer the row as it stands.
                recorded = await run_in_threadpool(mcp_approvals.get_visible, row["id"],
                                                   viewer=current_user, cfg=cfg)
            row = recorded or row
        return mcp_approvals.approval_public(row)

    @router.post("/mcp/approval-devices", operation_id="register_mcp_approval_device", status_code=201,
                 openapi_extra=mcp_policy("never"))
    def api_register_mcp_approval_device(payload: dict = Body(...), current_user: dict = authed):
        """Enrol an approval device's P-256 public key; it can sign decisions 10 minutes later."""
        _refuse_mcp_principal(current_user)
        try:
            row = mcp_approvals.register_device(user=current_user, name=payload.get("name"),
                                                public_key_b64=payload.get("public_key"))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        mcp_approvals.alert_device_registered(row, current_user.get("username"))
        return {"device_id": row["id"], "active_at": row["active_at"]}

    @router.get("/mcp/approval-devices", operation_id="list_mcp_approval_devices",
                openapi_extra=mcp_policy("never"))
    def api_list_mcp_approval_devices(current_user: dict = authed):
        """The caller's approval devices that are not revoked."""
        _refuse_mcp_principal(current_user)
        rows = mcp_approvals.list_devices(current_user.get("id"))
        return {"devices": [mcp_approvals.device_public(row) for row in rows]}

    @router.delete("/mcp/approval-devices/{device_id}", operation_id="revoke_mcp_approval_device",
                   status_code=204, openapi_extra=mcp_policy("never"))
    def api_revoke_mcp_approval_device(device_id: str, current_user: dict = authed) -> None:
        """Revoke one of the caller's approval devices; it can no longer sign decisions."""
        _refuse_mcp_principal(current_user)
        if not mcp_approvals.revoke_device(user_id=current_user.get("id"), device_id=device_id):
            raise HTTPException(status_code=404, detail=DEVICE_NOT_FOUND_DETAIL)
        # None, not a Response: FastAPI sends an empty 204 and keeps the session's
        # X-Refreshed-Token header (get_current_user).

    service = [Depends(require_mcp_service)]

    @router.get("/internal/mcp/openapi.json", operation_id="mcp_internal_openapi",
                openapi_extra=mcp_policy("never"), include_in_schema=False, dependencies=service)
    def api_mcp_internal_openapi(request: Request):
        """The OpenAPI document of the operations an MCP caller may reach, with its schema hash."""
        return build_internal_openapi(request.app)

    @router.post("/internal/mcp/verify-password", operation_id="mcp_internal_verify_password",
                 openapi_extra=mcp_policy("never"), include_in_schema=False, dependencies=service)
    def api_mcp_internal_verify_password(body: VerifyPasswordBody, request: Request):
        """Check a password for the MCP server's own login, over the login limiter keyed (username, "mcp").

        Failures do not count per host across usernames (per_ip=False): every MCP
        login shares the one "host", so that count would let a spray of made-up
        names lock out every real one.
        """
        operation_id = "mcp_internal_verify_password"
        username = body.username.strip().lower()
        audit = {"username": username, "args_summary": summarize_args({}, {"username": username})}
        if auth_utils.login_is_rate_limited(username, MCP_LIMITER_HOST, per_ip=False):
            retry_after_s = auth_utils.login_rate_limit_config()[1]
            _internal_audit(request, outcome="verify_password_limited", status=429,
                            operation_id=operation_id, **audit)
            return JSONResponse({"detail": TOO_MANY_ATTEMPTS_DETAIL, "retry_after_s": retry_after_s},
                                status_code=429, headers={"Retry-After": str(retry_after_s)})
        user = auth_utils.get_user_by_username(None, username)
        stored = user.get("password_hash") if isinstance(user, dict) else None
        if isinstance(stored, str) and stored:
            ok = auth_utils.verify_password(body.password, stored)
        else:
            auth_utils.verify_password(body.password, _dummy_bcrypt_hash())
            ok = False
        if not ok:
            auth_utils.record_failed_login(username, MCP_LIMITER_HOST, per_ip=False)
            _internal_audit(request, outcome="verify_password_failed", status=401,
                            operation_id=operation_id, **audit)
            raise HTTPException(status_code=401, detail=INVALID_CREDENTIALS_DETAIL)
        auth_utils.clear_login_attempts(username, MCP_LIMITER_HOST)
        _internal_audit(request, outcome="verify_password_ok", status=200, operation_id=operation_id,
                        user_id=user.get("id"), **audit)
        return {"user_id": user.get("id"), "username": user.get("username")}

    @router.post("/internal/mcp/audit-event", operation_id="mcp_internal_audit_event", status_code=204,
                 openapi_extra=mcp_policy("never"), include_in_schema=False, dependencies=service)
    def api_mcp_internal_audit_event(request: Request, payload: dict = Body(...)) -> None:
        """Store one auth event from the MCP server; relay the security ones to Discord."""
        event = payload.get("event")
        detail = payload.get("detail")
        if payload.get("kind") != "auth":
            raise HTTPException(status_code=400, detail="kind must be auth")
        if not isinstance(event, str) or event not in AUTH_EVENTS:
            raise HTTPException(status_code=400, detail="unknown auth event")
        if not isinstance(detail, dict):
            raise HTTPException(status_code=400, detail="detail must be an object")
        summary = _auth_detail_summary(detail)
        _internal_audit(request, outcome="auth_event", status=204, operation_id=f"auth_event:{event}",
                        args_summary=summary)
        if event in DISCORD_RELAYED_EVENTS:
            _relay_auth_event(event, summary)

    return router
