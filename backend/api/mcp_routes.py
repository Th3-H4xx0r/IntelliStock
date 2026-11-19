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
"""
from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter, Body, Depends, HTTPException

import mcp_approvals
from api.mcp_gate import NOT_AVAILABLE_DETAIL
from mcp_policy import McpConfig, mcp_policy

LIST_LIMIT_MIN = 1
LIST_LIMIT_MAX = 200
NOT_FOUND_DETAIL = "approval not found"
DEVICE_NOT_FOUND_DETAIL = "device not found"


def _refuse_mcp_principal(current_user: dict) -> None:
    if current_user.get("via") == "mcp":
        raise HTTPException(status_code=403, detail=NOT_AVAILABLE_DETAIL)


def build_router(current_user_dependency: Callable) -> APIRouter:
    """The /mcp/approvals and /mcp/approval-devices routes, closed over the API's
    session dependency."""
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

    return router
