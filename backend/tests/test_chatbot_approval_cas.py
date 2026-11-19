"""Compare-and-swap for chatbot conversation messages (spec section 7.5).

A tool approval reads the conversation, sees the message pending, then writes
it back resolved. Two approvals that read before either wrote both saw
"pending", and both ran the tool -- which can be submit_live_command. These
tests pin the primitive the approval paths claim through:
``conversations.transition_message``, built on ``store.replace_if`` guarded by
the doc's ``updated_at`` and a ``rev`` token every CAS write rewrites.

The helpers below (``conv``, ``_seed``, ``_StoreProxy``, ``_RacingStore``) are
written for reuse by the approval-path tests (mcp_bridge, orchestration).
"""
import os
import sys
import threading

import pytest

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)


@pytest.fixture
def conv(store, monkeypatch):
    """chatbot.conversations, reading and writing through the conftest store."""
    import chatbot.conversations as conversations

    monkeypatch.setattr(conversations, "store", store)
    return conversations


def _seed(conv, store, *, status="pending_confirmation", source="claude-cli-mcp"):
    """A conversation of user "u1" holding one assistant message that waits on
    a fake_write approval. Written with store.update: append_messages is SQL,
    which the FakeStore cannot run. Returns (conv_id, message_id)."""
    doc = conv.create_conversation(None, "u1")
    msg = conv.new_message(
        "assistant",
        tool_calls=[{"id": "tc-1", "name": "fake_write", "arguments": {"qty": 1}}],
        pending_tool={"tool_call_id": "tc-1", "name": "fake_write",
                      "arguments": {"qty": 1}, "safety": "write",
                      "description": "", "source": source},
        status=status,
    )
    store.update(conv.TABLE, doc["id"], {"messages": [msg], "message_count": 1})
    return doc["id"], msg["id"]


class _StoreProxy:
    """Delegates every attribute to ``inner`` and intercepts ``replace_if``:
    counts the calls, runs ``before_first_replace_if`` once just before the
    first delegated one (an interleaved write), or, with ``always_lose``,
    reports every swap as lost without delegating."""

    def __init__(self, inner, *, before_first_replace_if=None, always_lose=False):
        self._inner = inner
        self._hook = before_first_replace_if
        self._always_lose = always_lose
        self.replace_if_calls = 0

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def replace_if(self, *args, **kwargs):
        self.replace_if_calls += 1
        if self._always_lose:
            return None
        hook, self._hook = self._hook, None
        if hook is not None:
            hook()
        return self._inner.replace_if(*args, **kwargs)


class _RacingStore:
    """Forces two threads to read the same conversation before either writes.

    Each thread's FIRST ``replace_if`` waits at a shared two-party barrier, so
    both have already read and built their write. Every delegated
    ``replace_if`` then runs under one lock, standing in for Postgres' row
    lock (FakeStore is not thread-safe)."""

    def __init__(self, inner):
        self._inner = inner
        self._barrier = threading.Barrier(2, timeout=5)
        self._lock = threading.Lock()
        self._seen = threading.local()

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def replace_if(self, *args, **kwargs):
        if not getattr(self._seen, "waited", False):
            self._seen.waited = True
            self._barrier.wait()
        with self._lock:
            return self._inner.replace_if(*args, **kwargs)


# ── transition_message / the CAS primitive ─────────────────────────────────


def test_transition_moves_only_from_the_expected_status(conv, store):
    cid, mid = _seed(conv, store)
    before = conv.transition_message(None, "u1", cid, mid, expect_status="pending_confirmation",
                                     changes={"status": "executing"})
    assert before["status"] == "pending_confirmation" and before["pending_tool"]["name"] == "fake_write"
    assert conv.transition_message(None, "u1", cid, mid, expect_status="pending_confirmation",
                                   changes={"status": "executing"}) is None
    msg = conv.find_message(conv.get_conversation(None, "u1", cid), mid)
    assert msg["status"] == "executing" and msg["id"] == mid


def test_transition_refuses_unknown_message_owner_or_conversation(conv, store):
    cid, mid = _seed(conv, store)
    kw = dict(expect_status="pending_confirmation", changes={"status": "executing"})
    assert conv.transition_message(None, "u1", cid, "nope", **kw) is None
    assert conv.transition_message(None, "u2", cid, mid, **kw) is None
    assert conv.transition_message(None, "u1", "missing-conv", mid, **kw) is None
    assert conv.find_message(conv.get_conversation(None, "u1", cid), mid)["status"] == "pending_confirmation"


def test_every_cas_write_rewrites_rev(conv, store):
    cid, mid = _seed(conv, store)
    conv.transition_message(None, "u1", cid, mid, expect_status="pending_confirmation", changes={"status": "executing"})
    rev1 = conv.get_conversation(None, "u1", cid)["rev"]
    conv.transition_message(None, "u1", cid, mid, expect_status="executing", changes={"status": "complete"})
    assert conv.get_conversation(None, "u1", cid)["rev"] not in (None, rev1)


def test_cas_retries_past_an_interleaved_write_without_losing_it(conv, store, monkeypatch):
    cid, mid = _seed(conv, store)
    proxy = _StoreProxy(store, before_first_replace_if=lambda: store.update(
        conv.TABLE, cid, {"title": "renamed", "updated_at": "2099-01-01T00:00:00Z"}))
    monkeypatch.setattr(conv, "store", proxy)
    assert conv.transition_message(None, "u1", cid, mid, expect_status="pending_confirmation",
                                   changes={"status": "executing"}) is not None
    doc = conv.get_conversation(None, "u1", cid)
    assert doc["title"] == "renamed" and conv.find_message(doc, mid)["status"] == "executing"
    assert proxy.replace_if_calls == 2


def test_cas_gives_up_after_cas_attempts(conv, store, monkeypatch):
    cid, mid = _seed(conv, store)
    proxy = _StoreProxy(store, always_lose=True)
    monkeypatch.setattr(conv, "store", proxy)
    with pytest.raises(conv.ConversationBusyError):
        conv.transition_message(None, "u1", cid, mid, expect_status="pending_confirmation", changes={"status": "executing"})
    assert proxy.replace_if_calls == conv.CAS_ATTEMPTS == 5
    assert issubclass(conv.ConversationBusyError, ValueError)


def test_transition_on_a_conversation_deleted_mid_swap_returns_none(conv, store, monkeypatch):
    cid, mid = _seed(conv, store)
    monkeypatch.setattr(conv, "store", _StoreProxy(store, before_first_replace_if=lambda: store.delete(conv.TABLE, cid)))
    assert conv.transition_message(None, "u1", cid, mid, expect_status="pending_confirmation", changes={"status": "executing"}) is None


def test_a_store_failure_mid_swap_is_not_read_as_a_deleted_row(conv, store, monkeypatch):
    """Only a vanished row means "gone". An outage while the row still exists
    must surface, not pass for an approval that was already resolved."""
    from db import UnavailableError

    cid, mid = _seed(conv, store)

    def outage():
        raise UnavailableError("postgres unavailable")

    monkeypatch.setattr(conv, "store", _StoreProxy(store, before_first_replace_if=outage))
    with pytest.raises(UnavailableError):
        conv.transition_message(None, "u1", cid, mid, expect_status="pending_confirmation",
                                changes={"status": "executing"})


def test_rev_separates_two_swaps_stamped_in_the_same_microsecond(conv, store, monkeypatch):
    """updated_at alone cannot tell two CAS writes apart when the clock repeats."""
    monkeypatch.setattr(conv, "_now", lambda: "2026-11-19T00:00:00.000000Z")
    cid, mid = _seed(conv, store)
    kw = dict(expect_status="pending_confirmation", changes={"status": "executing"})
    rival = []
    monkeypatch.setattr(conv, "store", _StoreProxy(store, before_first_replace_if=lambda: rival.append(
        conv.transition_message(None, "u1", cid, mid, **kw))))
    assert conv.transition_message(None, "u1", cid, mid, **kw) is None
    assert rival[0] is not None


def test_racing_transitions_have_exactly_one_winner(conv, store, monkeypatch):
    cid, mid = _seed(conv, store)
    monkeypatch.setattr(conv, "store", _RacingStore(store))
    results = []

    def claim():
        results.append(conv.transition_message(None, "u1", cid, mid, expect_status="pending_confirmation",
                                               changes={"status": "executing"}))

    threads = [threading.Thread(target=claim) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert len(results) == 2 and [r is None for r in results].count(True) == 1
    assert conv.find_message(conv.get_conversation(None, "u1", cid), mid)["status"] == "executing"


def test_replace_message_keeps_the_id_and_rewrites_rev(conv, store):
    cid, mid = _seed(conv, store)
    doc = conv.replace_message(None, "u1", cid, mid, {"id": "other", "role": "assistant", "content": "x", "status": "complete"})
    assert conv.find_message(doc, mid)["content"] == "x" and doc["rev"]
    assert conv.replace_message(None, "u1", cid, "nope", {"content": "y"}) is None


def test_concurrent_append_survives_a_transition(conv, store, monkeypatch):
    from db.fake import FakeStore
    if isinstance(store, FakeStore):
        pytest.skip("append_messages is SQL; needs PG_TEST_DSN")
    cid, mid = _seed(conv, store)
    monkeypatch.setattr(conv, "store", _StoreProxy(store, before_first_replace_if=lambda: conv.append_messages(
        None, "u1", cid, [conv.new_message("assistant", content="late reply")])))
    conv.transition_message(None, "u1", cid, mid, expect_status="pending_confirmation", changes={"status": "executing"})
    doc = conv.get_conversation(None, "u1", cid)
    assert "late reply" in [m.get("content") for m in doc["messages"]]
    assert conv.find_message(doc, mid)["status"] == "executing"
