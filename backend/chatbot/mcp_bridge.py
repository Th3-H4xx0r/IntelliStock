"""Server-side glue between IntelliStock's tool registry and the
``claude_cli`` MCP bridge.

The flow:

  * Safe tools (``safety == "safe"``) run synchronously and the result
    is handed back to CC.
  * Non-safe tools (``write`` / ``destructive``) append a
    ``pending_confirmation`` message to the conversation and return a
    ``{ok: false, pending: true, ...}`` envelope so CC can acknowledge
    the queued action in its assistant reply. The user then approves or
    declines via :func:`resolve_mcp_pending`, which either executes the
    queued action (appending a tool-result message) or records the
    decline.

The user is *not* logged in to the IntelliStock backend from the MCP
server's perspective — auth is via the per-session token. We look up
the IntelliStock user record by id (threaded in by
``call_claude_cli_chat``) so tools that need username / role
attribution see the right principal.
"""
from __future__ import annotations

import json
import sys
import uuid
from typing import Any, Dict, List, Optional

from auth_utils import get_user_by_id          # type: ignore[import-not-found]

from chatbot import conversations as conv_store
from chatbot.tools import get_tool


def _log(msg: str, color: str = "white") -> None:
    try:
        from intellistock_logger import intellistock_logger
        intellistock_logger.log(msg, color, service="McpBridge")
    except Exception:
        try:
            print(f"[McpBridge] {msg}", file=sys.stderr, flush=True)
        except Exception:
            pass


# CC's MCP integration sometimes calls tools with the namespaced
# ``mcp__<server>__<name>`` shape (the form the model sees), other
# times with the bare ``<name>``. Strip the namespace prefix
# defensively so our registry lookup works regardless of which form
# came in.
_MCP_NAME_PREFIX = "mcp__intellistock__"


def _normalize_tool_name(name: str) -> str:
    if not name:
        return ""
    if name.startswith(_MCP_NAME_PREFIX):
        return name[len(_MCP_NAME_PREFIX):]
    return name


# ── Public dispatch ───────────────────────────────────────────────────────


def dispatch_mcp_tool_call(
    *,
    conn,
    user_id: str,
    conversation_id: str,
    tool_name: str,
    arguments: Dict[str, Any],
) -> Dict[str, Any]:
    """Entry point for the MCP server. Returns one of:

      * ``{ok: true, result: <serialisable>}`` — tool executed.
      * ``{ok: false, pending: true, error: "...",
            message_id: <pending row id>}`` — queued for user
        confirmation. CC will see this as a failure with a clear
        message so it can apologise / inform the user.
      * ``{ok: false, error: "..."}`` — unknown tool / executor crash.
    """
    normalized = _normalize_tool_name(tool_name)
    tool = get_tool(normalized)
    _log(
        f"dispatch tool_name={tool_name!r} normalized={normalized!r} "
        f"user_id={user_id!r} conv={conversation_id[:8]} "
        f"tool_found={tool is not None} "
        f"safety={tool.safety if tool else 'n/a'}",
        "cyan",
    )
    if tool is None:
        return {"ok": False, "error": f"unknown tool: {tool_name!r}"}

    user = _load_user(conn, user_id)
    if user is None:
        return {
            "ok": False,
            "error": f"user {user_id!r} not found — cannot dispatch tools.",
        }

    if tool.safety == "safe":
        result = _execute_tool(conn=conn, user=user, tool_name=normalized, arguments=arguments)
        _log(f"safe-tool result ok={result.get('ok')} keys={list(result.keys())}", "cyan")
        return result

    # Non-safe: queue a pending_confirmation row on the conversation and
    # return a "pending" envelope to CC. The user will approve/decline
    # via /chatbot/conversations/{id}/mcp-confirm which calls
    # resolve_mcp_pending below.
    convo = conv_store.get_conversation(conn, user["id"], conversation_id)
    if not convo:
        return {
            "ok": False,
            "error": f"conversation {conversation_id} not found for user {user['id']!r}.",
        }

    pending_payload = {
        "tool_call_id": f"mcp-{uuid.uuid4().hex[:12]}",
        "name": normalized,
        "arguments": arguments,
        "safety": tool.safety,
        "description": tool.description,
        "source": "claude-cli-mcp",
    }
    pending_msg = conv_store.new_message(
        "assistant",
        content=(
            f"⚠️ I tried to run the **{normalized}** tool ({tool.safety}); "
            "this requires your confirmation before it can execute. "
            "Approve or decline below to continue."
        ),
        tool_calls=[{
            "id": pending_payload["tool_call_id"],
            "name": normalized,
            "arguments": arguments,
        }],
        pending_tool=pending_payload,
        status="pending_confirmation",
    )
    conv_store.append_messages(conn, user["id"], conversation_id, [pending_msg])
    _log(
        f"queued tool={normalized} safety={tool.safety} "
        f"pending_id={pending_msg.get('id')!r}",
        "yellow",
    )

    return {
        "ok": False,
        "pending": True,
        "message_id": pending_msg.get("id"),
        "error": (
            f"Tool {normalized} is queued for user confirmation. "
            "The action has been surfaced to the user; tell them the "
            "request was logged and that they need to approve or decline."
        ),
    }


def resolve_mcp_pending(
    *,
    conn,
    user: Dict[str, Any],
    conversation_id: str,
    message_id: str,
    approved: bool,
) -> List[Dict[str, Any]]:
    """User-confirm path. Find the queued pending row, execute (or
    decline) the captured tool call, and append result messages to the
    conversation. Returns the new messages so the API layer can hand
    them back to the frontend for immediate rendering."""
    user_id = user.get("id")
    if not user_id:
        raise ValueError("authenticated user has no id")
    convo = conv_store.get_conversation(conn, user_id, conversation_id)
    if not convo:
        raise ValueError(f"conversation {conversation_id} not found")
    pending_msg = conv_store.find_message(convo, message_id)
    if not pending_msg or pending_msg.get("status") != "pending_confirmation":
        raise ValueError(f"no pending tool found on message {message_id!r}")
    pending_payload = pending_msg.get("pending_tool") or {}
    if pending_payload.get("source") != "claude-cli-mcp":
        raise ValueError(
            "mcp-confirm endpoint received a pending row that wasn't produced by claude-cli; "
            "use /chatbot/conversations/{id}/confirm-tool for the standard flow."
        )

    tool_name = pending_payload.get("name") or ""
    arguments = pending_payload.get("arguments") or {}
    tool_call_id = pending_payload.get("tool_call_id") or message_id

    appended: List[Dict[str, Any]] = []

    if not approved:
        # Resolve the pending row to "declined" so the UI stops showing
        # the confirm card, then append a tool-result message recording
        # the decline. We don't push that result back to CC; the CC
        # session is idle and the user will summarise on the next turn
        # if they want CC to incorporate the outcome.
        resolved = {
            **pending_msg,
            "status": "complete",
            "pending_tool": None,
        }
        conv_store.replace_message(conn, user_id, conversation_id, message_id, resolved)
        decline_msg = conv_store.new_message(
            "tool",
            content=json.dumps({
                "error": "user_declined",
                "tool": tool_name,
                "via": "claude-cli-mcp",
            }),
            tool_call_id=tool_call_id,
            name=tool_name,
        )
        appended.append(decline_msg)
        conv_store.append_messages(conn, user_id, conversation_id, appended)
        return appended

    # Approved: execute and append the result.
    exec_result = _execute_tool(
        conn=conn, user=user, tool_name=tool_name, arguments=arguments,
    )
    # Build the human-facing tool message + any UI render blocks.
    tool_msg, blocks = _build_tool_result_message(
        tool_call_id=tool_call_id,
        tool_name=tool_name,
        exec_result=exec_result,
    )
    if blocks:
        tool_msg["blocks"] = blocks

    resolved = {
        **pending_msg,
        "status": "complete",
        "pending_tool": None,
    }
    conv_store.replace_message(conn, user_id, conversation_id, message_id, resolved)
    appended.append(tool_msg)
    conv_store.append_messages(conn, user_id, conversation_id, appended)
    return appended


# ── Internal helpers ──────────────────────────────────────────────────────


def _load_user(conn, user_id: str) -> Optional[Dict[str, Any]]:
    if not user_id:
        return None
    try:
        doc = get_user_by_id(conn, user_id)
    except Exception:
        return None
    if not doc:
        return None
    return {
        "id": doc.get("id"),
        "username": doc.get("username"),
        "role": doc.get("role", "user"),
    }


def _execute_tool(
    *, conn, user: Dict[str, Any], tool_name: str, arguments: Dict[str, Any],
) -> Dict[str, Any]:
    tool = get_tool(tool_name)
    if tool is None:
        return {"ok": False, "error": f"unknown tool: {tool_name!r}"}
    try:
        raw = tool.executor({"conn": conn, "user": user}, arguments or {})
    except Exception as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "result": raw}


def _build_tool_result_message(
    *, tool_call_id: str, tool_name: str, exec_result: Dict[str, Any],
):
    """Same shape as orchestration._build_tool_result_message but for the
    standalone MCP path. Extracts UI blocks from render-only tools so the
    chart / table / navigate block renders alongside the tool message."""
    blocks: List[Dict[str, Any]] = []
    payload: Any = exec_result.get("result") if exec_result.get("ok") else {"error": exec_result.get("error")}

    if isinstance(payload, dict):
        if payload.get("type") == "block":
            blocks.append(payload["block"])
            payload = {"ok": True, "rendered": payload["block"].get("type")}
        elif payload.get("type") == "navigate":
            blocks.append({"type": "navigate", "route": payload.get("route")})
            payload = {"ok": True, "navigated_to": payload.get("route")}

    try:
        content_text = json.dumps(payload, default=str)
    except Exception:
        content_text = str(payload)
    if len(content_text) > 4000:
        content_text = content_text[:4000] + "... (truncated)"

    msg = conv_store.new_message(
        "tool",
        content=content_text,
        tool_call_id=tool_call_id,
        name=tool_name,
    )
    return msg, blocks


__all__ = ["dispatch_mcp_tool_call", "resolve_mcp_pending"]
