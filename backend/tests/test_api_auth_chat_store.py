"""G9 store semantics behind api/main.py, auth_utils.py and chatbot/*.

The brief named tables ("Sessions", "Conversations") and an index ("email")
that the shipped registry does not have. db/schema.py is the authority, so
these use the real ones: ChatbotConversations (indexed on user_id) and Users
(indexed on username).
"""
import os
import sys

import pytest

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

CONV = "ChatbotConversations"


def test_delete_on_a_selection_is_one_statement(store):
    store.insert(CONV, [{"id": str(i), "user_id": "u1"} for i in range(3)]
                       + [{"id": "keep", "user_id": "u2"}])
    res = store.delete(CONV, store.filter(CONV, {"user_id": "u1"}))
    assert res["deleted"] == 3
    assert store.get(CONV, "keep") is not None


def test_conversation_order_is_bytewise_desc(store):
    for cid, ts in [("a", "2026-08-01"), ("b", "2026-08-02"), ("c", "2026-08-03")]:
        store.insert(CONV, {"id": cid, "user_id": "u1", "updated_at": ts})
    sel = store.order_by(store.filter(CONV, {"user_id": "u1"}),
                         fields=(store.desc("updated_at"),))
    assert [row["id"] for row in store.run(store.limit(sel, 2))] == ["c", "b"]


def test_auth_lookup_by_index_returns_no_duplicates_for_distinct_keys(store):
    store.insert("Users", [{"id": "1", "username": "a"}, {"id": "2", "username": "b"}])
    rows = store.get_all("Users", "a", "b", index="username")
    assert sorted(row["id"] for row in rows) == ["1", "2"]
    # ...but get_all does NOT dedupe repeated keys:
    assert len(store.get_all("Users", "a", "a", index="username")) == 2


def test_llm_usage_ts_window_is_half_open_and_orders_numerically(store):
    """LLMUsage.ts is epoch MILLISECONDS, so every realistic value is exactly
    13 digits and a bytewise text compare on the generated column agrees with
    a numeric one. api/main.py stringifies the bounds for that reason -- an
    int bound would ask Postgres to compare text against integer.
    """
    for ms in (1_700_000_000_000, 1_700_000_000_001, 1_700_000_000_002):
        store.insert("LLMUsage", {"id": str(ms), "ts": ms, "provider": "azure"})
    sel = store.between("LLMUsage", "1700000000000", "1700000000002", index="ts")
    got = sorted(int(row["ts"]) for row in store.run(sel))
    assert got == [1_700_000_000_000, 1_700_000_000_001]     # [lo, hi), never BETWEEN
    ordered = store.run(store.order_by(store.Selection("LLMUsage"),
                                       index="ts", desc=True))
    assert [int(r["ts"]) for r in ordered] == [1_700_000_000_002,
                                               1_700_000_000_001,
                                               1_700_000_000_000]


def test_auth_users_round_trip_through_the_module(store, monkeypatch):
    import auth_utils

    monkeypatch.setattr(auth_utils, "store", store)
    monkeypatch.setattr(auth_utils, "ensure_users_table", lambda conn=None: None)
    doc = auth_utils.create_user(None, "Alice", "hunter2", role="admin")
    assert doc["username"] == "alice" and "password_hash" not in doc
    assert auth_utils.get_user_by_username(None, "ALICE")["id"] == doc["id"]
    assert auth_utils.get_user_by_id(None, doc["id"])["role"] == "admin"
    assert [u["id"] for u in auth_utils.list_users(None)] == [doc["id"]]
    with pytest.raises(ValueError):
        auth_utils.create_user(None, "alice", "hunter2")
    auth_utils.delete_user(None, doc["id"])
    with pytest.raises(ValueError):
        auth_utils.delete_user(None, doc["id"])


def test_append_messages_is_atomic_and_derives_the_counter(store, monkeypatch):
    """The append must not be a read-modify-write: two writers would lose one
    batch. Needs real SQL, so it is skipped on the FakeStore."""
    from db.fake import FakeStore
    if isinstance(store, FakeStore):
        pytest.skip("jsonb array append needs PG_TEST_DSN")
    import chatbot.conversations as conv

    monkeypatch.setattr(conv, "store", store)
    doc = conv.create_conversation(None, "u1")
    conv.append_messages(None, "u1", doc["id"], [conv.new_message("user", "hi")])
    conv.append_messages(None, "u1", doc["id"], [conv.new_message("assistant", "yo")])
    fresh = conv.get_conversation(None, "u1", doc["id"])
    assert [m["content"] for m in fresh["messages"]] == ["hi", "yo"]
    assert fresh["message_count"] == 2
    assert fresh["last_message_preview"] == "yo"


def test_conversations_are_scoped_to_their_owner(store, monkeypatch):
    import chatbot.conversations as conv

    monkeypatch.setattr(conv, "store", store)
    mine = conv.create_conversation(None, "u1", title="mine")
    assert conv.get_conversation(None, "u2", mine["id"]) is None
    assert conv.delete_conversation(None, "u2", mine["id"]) is False
    assert [c["id"] for c in conv.list_conversations(None, "u1")] == [mine["id"]]
    assert conv.list_conversations(None, "u2") == []
