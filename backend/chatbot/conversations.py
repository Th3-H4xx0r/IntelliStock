"""Chatbot conversation storage (RethinkDB).

The first per-user resource in this codebase: conversations are scoped by
``user_id`` and queried via a secondary index. Conversations carry their
selected model + a flat list of message documents so a single ``get`` is
enough to render the full thread.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from rethinkdb import RethinkDB

r = RethinkDB()
DB_NAME = os.environ.get("INTELLISTOCK_DB_NAME", "IntelliStock")
TABLE = "ChatbotConversations"
USER_INDEX = "user_id"

# Hard cap so a runaway loop can't blow up a single conversation document.
# Older messages are still preserved; the LLM context is trimmed separately.
MAX_MESSAGES_PER_CONVERSATION = 2000


def _now() -> str:
    return datetime.utcnow().isoformat() + "Z"


def ensure_chatbot_tables(conn) -> None:
    """Create the ChatbotConversations table + user_id index if needed."""
    dbs = list(r.db_list().run(conn))
    if DB_NAME not in dbs:
        r.db_create(DB_NAME).run(conn)
    tables = list(r.db(DB_NAME).table_list().run(conn))
    if TABLE not in tables:
        r.db(DB_NAME).table_create(TABLE).run(conn)
    indexes = list(r.db(DB_NAME).table(TABLE).index_list().run(conn))
    if USER_INDEX not in indexes:
        r.db(DB_NAME).table(TABLE).index_create(USER_INDEX).run(conn)
        r.db(DB_NAME).table(TABLE).index_wait(USER_INDEX).run(conn)


# ── Public message envelope ────────────────────────────────────────────────


def _new_message(
    role: str,
    content: str = "",
    blocks: Optional[List[Dict[str, Any]]] = None,
    tool_calls: Optional[List[Dict[str, Any]]] = None,
    tool_call_id: Optional[str] = None,
    name: Optional[str] = None,
    pending_tool: Optional[Dict[str, Any]] = None,
    status: str = "complete",
) -> Dict[str, Any]:
    msg: Dict[str, Any] = {
        "id": str(uuid.uuid4()),
        "role": role,
        "content": content,
        "created_at": _now(),
        "status": status,
    }
    if blocks:
        msg["blocks"] = blocks
    if tool_calls:
        msg["tool_calls"] = tool_calls
    if tool_call_id:
        msg["tool_call_id"] = tool_call_id
    if name:
        msg["name"] = name
    if pending_tool:
        msg["pending_tool"] = pending_tool
    return msg


# ── CRUD ───────────────────────────────────────────────────────────────────


def _require_user_id(user_id: str) -> str:
    """Reject empty / falsy user ids before they hit a query — defence in depth
    so a future code path (test fixture, missing JWT claim, etc.) can't end up
    matching a conversation row whose user_id is also blank."""
    if not user_id or not isinstance(user_id, str) or not user_id.strip():
        raise ValueError("user_id is required")
    return user_id


def list_conversations(conn, user_id: str) -> List[Dict[str, Any]]:
    _require_user_id(user_id)
    cursor = (
        r.db(DB_NAME)
        .table(TABLE)
        .get_all(user_id, index=USER_INDEX)
        .pluck(
            "id",
            "title",
            "model_id",
            "model_name",
            "created_at",
            "updated_at",
            "last_message_preview",
            "message_count",
        )
        .order_by(r.desc("updated_at"))
        .run(conn)
    )
    return list(cursor)


def get_conversation(conn, user_id: str, conv_id: str) -> Optional[Dict[str, Any]]:
    _require_user_id(user_id)
    doc = r.db(DB_NAME).table(TABLE).get(conv_id).run(conn)
    if not doc or doc.get("user_id") != user_id:
        return None
    doc.setdefault("messages", [])
    return doc


def create_conversation(
    conn,
    user_id: str,
    *,
    model_id: Optional[str] = None,
    model_name: Optional[str] = None,
    title: Optional[str] = None,
) -> Dict[str, Any]:
    _require_user_id(user_id)
    now = _now()
    doc = {
        "id": str(uuid.uuid4()),
        "user_id": user_id,
        "title": title or "New conversation",
        "model_id": model_id,
        "model_name": model_name,
        "messages": [],
        "message_count": 0,
        "last_message_preview": "",
        "settings": {
            "auto_confirm_safe_tools": True,
        },
        "created_at": now,
        "updated_at": now,
    }
    r.db(DB_NAME).table(TABLE).insert(doc).run(conn)
    return doc


def update_conversation(
    conn,
    user_id: str,
    conv_id: str,
    *,
    title: Optional[str] = None,
    model_id: Optional[str] = None,
    model_name: Optional[str] = None,
    settings: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    doc = get_conversation(conn, user_id, conv_id)
    if not doc:
        return None
    update: Dict[str, Any] = {"updated_at": _now()}
    if title is not None:
        update["title"] = title.strip()[:200] or "New conversation"
    if model_id is not None:
        update["model_id"] = model_id
    if model_name is not None:
        update["model_name"] = model_name
    if settings is not None:
        merged = {**(doc.get("settings") or {}), **settings}
        update["settings"] = merged
    r.db(DB_NAME).table(TABLE).get(conv_id).update(update).run(conn)
    return get_conversation(conn, user_id, conv_id)


def delete_conversation(conn, user_id: str, conv_id: str) -> bool:
    doc = get_conversation(conn, user_id, conv_id)
    if not doc:
        return False
    r.db(DB_NAME).table(TABLE).get(conv_id).delete().run(conn)
    return True


def clear_messages(conn, user_id: str, conv_id: str) -> Optional[Dict[str, Any]]:
    doc = get_conversation(conn, user_id, conv_id)
    if not doc:
        return None
    r.db(DB_NAME).table(TABLE).get(conv_id).update({
        "messages": [],
        "message_count": 0,
        "last_message_preview": "",
        "updated_at": _now(),
    }).run(conn)
    return get_conversation(conn, user_id, conv_id)


def _trim_to_max(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Trim a message list to ``MAX_MESSAGES_PER_CONVERSATION`` without
    splitting an assistant ↔ tool tool_call_id pair. We always start the
    retained window at a non-tool message so OpenAI's API doesn't 400 on
    an orphaned tool message that lost its parent assistant turn."""
    if len(messages) <= MAX_MESSAGES_PER_CONVERSATION:
        return messages
    start = len(messages) - MAX_MESSAGES_PER_CONVERSATION
    while start < len(messages) and messages[start].get("role") == "tool":
        start += 1
    return messages[start:]


def append_messages(
    conn,
    user_id: str,
    conv_id: str,
    messages: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Append validated messages to a conversation atomically.

    Uses RethinkDB's ``r.row['messages'].add(new)`` so two concurrent writers
    can't clobber each other (read-modify-write would lose the earlier
    write's appends). Also derives the preview / counter inside the same
    atomic update."""
    _require_user_id(user_id)
    if not messages:
        return get_conversation(conn, user_id, conv_id)
    # Verify user owns the conversation BEFORE writing.
    doc = get_conversation(conn, user_id, conv_id)
    if not doc:
        return None

    # Derive a preview from the new batch alone; if no user/assistant text,
    # fall back to the existing preview by leaving it untouched at update time.
    preview = ""
    for m in reversed(messages):
        if m.get("role") in ("user", "assistant") and m.get("content"):
            preview = str(m["content"])[:240]
            break

    update_doc: Dict[str, Any] = {
        "messages": r.row["messages"].default([]).add(messages),
        "message_count": r.row["messages"].default([]).count().add(len(messages)),
        "updated_at": _now(),
    }
    if preview:
        update_doc["last_message_preview"] = preview
    r.db(DB_NAME).table(TABLE).get(conv_id).update(update_doc).run(conn)

    # Refresh and only trim if we crossed the cap. Trimming is non-atomic but
    # idempotent — the worst-case is two writers each trim once.
    fresh = get_conversation(conn, user_id, conv_id)
    if fresh and len(fresh.get("messages") or []) > MAX_MESSAGES_PER_CONVERSATION:
        trimmed = _trim_to_max(fresh["messages"])
        r.db(DB_NAME).table(TABLE).get(conv_id).update({
            "messages": trimmed,
            "message_count": len(trimmed),
        }).run(conn)
        fresh = get_conversation(conn, user_id, conv_id)
    return fresh


def replace_message(
    conn,
    user_id: str,
    conv_id: str,
    message_id: str,
    new_message: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """Replace a single message by id (used to resolve pending tool calls)."""
    doc = get_conversation(conn, user_id, conv_id)
    if not doc:
        return None
    msgs = doc.get("messages") or []
    found = False
    for i, m in enumerate(msgs):
        if m.get("id") == message_id:
            preserved_id = m.get("id")
            new_message = {**new_message, "id": preserved_id}
            msgs[i] = new_message
            found = True
            break
    if not found:
        return None
    r.db(DB_NAME).table(TABLE).get(conv_id).update({
        "messages": msgs,
        "updated_at": _now(),
    }).run(conn)
    return get_conversation(conn, user_id, conv_id)


def find_message(doc: Dict[str, Any], message_id: str) -> Optional[Dict[str, Any]]:
    for m in (doc.get("messages") or []):
        if m.get("id") == message_id:
            return m
    return None


# Re-export the message constructor for orchestration code.
new_message = _new_message
