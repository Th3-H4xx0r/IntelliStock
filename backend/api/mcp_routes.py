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
"""
from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter, Depends, HTTPException

import mcp_approvals
from mcp_policy import McpConfig, mcp_policy

LIST_LIMIT_MIN = 1
LIST_LIMIT_MAX = 200
NOT_FOUND_DETAIL = "approval not found"


def build_router(current_user_dependency: Callable) -> APIRouter:
    """The /mcp/approvals routes, closed over the API's session dependency."""
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

    return router
