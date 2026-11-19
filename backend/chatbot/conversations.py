"""Chatbot conversation storage (Postgres).

The first per-user resource in this codebase: conversations are scoped by
``user_id`` and queried via a secondary index. Conversations carry their
selected model + a flat list of message documents so a single ``get`` is
enough to render the full thread. ``rev`` is an opaque token rewritten by
every compare-and-swap write.
"""
from __future__ import annotations

import copy
import os
import uuid
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from db import P, StoreError
from db import json as db_json
from db import schema as db_schema
from db import store

DB_NAME = os.environ.get("INTELLISTOCK_DB_NAME", "IntelliStock")
TABLE = "ChatbotConversations"
USER_INDEX = "user_id"

# Hard cap so a runaway loop can't blow up a single conversation document.
# Older messages are still preserved; the LLM context is trimmed separately.
MAX_MESSAGES_PER_CONVERSATION = 2000

# Message statuses a tool approval moves through. A message is claimed
# (pending -> executing) before its tool runs, so a second approval that read
# "pending" loses the swap instead of running the tool again.
STATUS_PENDING = "pending_confirmation"
STATUS_EXECUTING = "executing"
STATUS_COMPLETE = "complete"

# Lost swaps tolerated before a write gives up with ConversationBusyError.
CAS_ATTEMPTS = 5


class ConversationBusyError(ValueError):
    """Every compare-and-swap attempt lost to a concurrent write."""


def _now() -> str:
    return datetime.utcnow().isoformat() + "Z"


def ensure_chatbot_tables(conn=None) -> None:
    """Create the ChatbotConversations table + user_id index if needed.

    R25: the index set lives in db/schema.py, which already declares
    ChatbotConversations with indexed_fields=("user_id",). ``conn`` is kept
    and ignored -- api/main.py's startup hook and _ensure_chatbot_ready both
    pass one.
    """
    db_schema.ensure_table(TABLE)


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
    # ORDER BY runs in Postgres under COLLATE "C" (R21), then pluck drops the
    # keys the list view does not need. pluck OMITS a missing key rather than
    # emitting null, exactly as ReQL did.
    sel = store.order_by(store.filter(TABLE, {USER_INDEX: user_id}),
                         fields=(store.desc("updated_at"),))
    return store.pluck(
        store.run(sel),
        "id",
        "title",
        "model_id",
        "model_name",
        "created_at",
        "updated_at",
        "last_message_preview",
        "message_count",
    )


def get_conversation(conn, user_id: str, conv_id: str) -> Optional[Dict[str, Any]]:
    _require_user_id(user_id)
    doc = store.get(TABLE, conv_id)
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
    store.insert(TABLE, doc)
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
    store.update(TABLE, conv_id, update)
    return get_conversation(conn, user_id, conv_id)


def delete_conversation(conn, user_id: str, conv_id: str) -> bool:
    doc = get_conversation(conn, user_id, conv_id)
    if not doc:
        return False
    store.delete(TABLE, conv_id)
    return True


def clear_messages(conn, user_id: str, conv_id: str) -> Optional[Dict[str, Any]]:
    doc = get_conversation(conn, user_id, conv_id)
    if not doc:
        return None
    store.update(TABLE, conv_id, {
        "messages": [],
        "message_count": 0,
        "last_message_preview": "",
        "updated_at": _now(),
    })
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

    The ReQL form was ``r.row['messages'].default([]).add(new)`` -- one
    server-side statement, so two concurrent writers can't clobber each other
    the way a read-modify-write would. store.update() deep-merges and
    REPLACES arrays wholesale, which is the opposite of what this needs, so
    this is one of the few sites that drops to hand-written SQL (spec 2.7:
    "a site needing more gets hand-written SQL in its owning module").
    ``doc`` inside an UPDATE ... SET expression is always the pre-update row,
    so message_count is derived from the old length exactly as ReQL did."""
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

    scalars: Dict[str, Any] = {"updated_at": _now()}
    if preview:
        scalars["last_message_preview"] = preview
    store.sql(
        'UPDATE "%s" SET doc = jsonb_set('
        "    jsonb_set(doc, '{messages}',"
        "              coalesce(doc->'messages', '[]'::jsonb) || %%(msgs)s::jsonb),"
        "    '{message_count}',"
        "    to_jsonb(coalesce("
        "        jsonb_array_length(coalesce(doc->'messages', '[]'::jsonb)), 0)"
        "      + %%(added)s)"
        ") || %%(scalars)s::jsonb, updated_at = now() WHERE id = %%(id)s" % TABLE,
        {"msgs": db_json.dumps(messages),
         "added": len(messages),
         "scalars": db_json.dumps(scalars),
         "id": store.coerce_id(TABLE, conv_id)},
    )

    # Trim only if we crossed the cap. The trim rewrites every message, so it
    # goes through the compare-and-swap: a plain write from a stale read could
    # put a claimed ("executing") message back to pending, and a later
    # approval would run its tool again.
    def trim(doc: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        msgs = doc.get("messages") or []
        if len(msgs) <= MAX_MESSAGES_PER_CONVERSATION:
            return None
        doc["messages"] = _trim_to_max(msgs)
        doc["message_count"] = len(doc["messages"])
        return doc

    fresh = get_conversation(conn, user_id, conv_id)
    if fresh and len(fresh.get("messages") or []) > MAX_MESSAGES_PER_CONVERSATION:
        try:
            _cas_conversation(user_id, conv_id, trim)
        except ConversationBusyError:
            # The batch is already written; the next append trims again.
            pass
        fresh = get_conversation(conn, user_id, conv_id)
    return fresh


def _token(ref, value):
    return ref.eq(str(value)) if value is not None else ref.is_null()


def _cas_conversation(
    user_id: str,
    conv_id: str,
    mutate: Callable[[Dict[str, Any]], Optional[Dict[str, Any]]],
) -> Optional[Dict[str, Any]]:
    """Read-modify-write one owner-scoped conversation as a compare-and-swap.

    ``mutate`` gets a deep copy of the doc and returns the doc to write, or
    None to write nothing. ``store.replace_if`` replaces the whole document,
    so the guard must catch every write since the read: ``updated_at`` catches
    append_messages / update_conversation / clear_messages, and ``rev``
    (rewritten only here) separates two CAS writes stamped in the same
    microsecond. A lost swap re-reads and retries.

    Returns the written doc; None when the conversation is missing or not the
    caller's, ``mutate`` declines, or the row was deleted mid-swap. Raises
    ConversationBusyError after CAS_ATTEMPTS lost swaps.
    """
    for _ in range(CAS_ATTEMPTS):
        doc = get_conversation(None, user_id, conv_id)
        if doc is None:
            return None
        new = mutate(copy.deepcopy(doc))
        if new is None:
            return None
        new["updated_at"], new["rev"] = _now(), uuid.uuid4().hex
        when = (_token(P.field("updated_at"), doc.get("updated_at"))
                & _token(P.field("rev"), doc.get("rev")))
        try:
            saved = store.replace_if(TABLE, conv_id, when=when, doc=new)
        except StoreError:
            # replace_if raises on a missing row. Only a row that is really
            # gone reads as None: an outage must not pass for "resolved".
            if store.get(TABLE, conv_id) is None:
                return None
            raise
        if saved is not None:
            return saved
    raise ConversationBusyError("conversation is busy; try again")


def transition_message(
    conn,
    user_id: str,
    conv_id: str,
    message_id: str,
    *,
    expect_status: str,
    changes: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """Move one message out of ``expect_status`` atomically.

    The new message is ``{**old, **changes, "id": message_id}``. Returns a
    copy of the message as it stood just before the transition; None when the
    conversation (owner-scoped) or message is missing, the message's status is
    not ``expect_status``, or the row vanished mid-swap. Of two callers racing
    the same transition, exactly one gets the message back.
    """
    before: List[Dict[str, Any]] = []

    def mutate(doc: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        msgs = doc.get("messages") or []
        for i, m in enumerate(msgs):
            if m.get("id") == message_id:
                if m.get("status") != expect_status:
                    return None
                before[:] = [copy.deepcopy(m)]
                msgs[i] = {**m, **changes, "id": message_id}
                return doc
        return None

    if _cas_conversation(user_id, conv_id, mutate) is None:
        return None
    return before[0]


def replace_message(
    conn,
    user_id: str,
    conv_id: str,
    message_id: str,
    new_message: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """Replace a single message by id (used to resolve pending tool calls).

    A compare-and-swap, so a concurrent append is retried past, not dropped."""
    def mutate(doc: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        msgs = doc.get("messages") or []
        for i, m in enumerate(msgs):
            if m.get("id") == message_id:
                msgs[i] = {**new_message, "id": message_id}
                return doc
        return None

    return _cas_conversation(user_id, conv_id, mutate)


def find_message(doc: Dict[str, Any], message_id: str) -> Optional[Dict[str, Any]]:
    for m in (doc.get("messages") or []):
        if m.get("id") == message_id:
            return m
    return None


# Re-export the message constructor for orchestration code.
new_message = _new_message
