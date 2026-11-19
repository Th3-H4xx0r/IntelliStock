"""Compare-and-swap for chatbot conversation messages (spec section 7.5).

A tool approval reads the conversation, sees the message pending, then writes
it back resolved. Two approvals that read before either wrote both saw
"pending", and both ran the tool -- which can be submit_live_command. These
tests pin the primitive the approval paths claim through:
``conversations.transition_message``, built on ``store.replace_if`` guarded by
the doc's ``updated_at`` and a ``rev`` token every CAS write rewrites.

The helpers below (``conv``, ``_seed``, ``_StoreProxy``, ``_RacingStore``,
``_race``) are written for reuse by the approval-path tests (mcp_bridge,
orchestration).
"""
import os
import sys
import threading
from types import SimpleNamespace

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


def _race(fn_a, fn_b):
    """Run fn_a and fn_b on two threads at once. Returns one entry per
    function, in argument order: ("ok", its return value) or ("err", the
    exception it raised)."""
    outcomes = [None, None]

    def run(i, fn):
        try:
            outcomes[i] = ("ok", fn())
        except Exception as e:
            outcomes[i] = ("err", e)

    threads = [threading.Thread(target=run, args=(i, fn)) for i, fn in enumerate((fn_a, fn_b))]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert None not in outcomes, "a racing call never finished"
    return outcomes


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


# ── resolve_mcp_pending: approvals claim before they execute ────────────────


def _mcp(conv, store, monkeypatch, *, racing=False, **seed):
    """Seed one fake_write approval and wire mcp_bridge to a recording tool.

    ``calls`` collects the tool's arguments per run and ``appended`` every
    message resolve_mcp_pending appends (append_messages is SQL, which the
    FakeStore cannot run). ``racing`` puts a _RacingStore under the
    conversation store; the single-thread tests keep the plain one, whose
    writes would otherwise wait at the two-party barrier."""
    from chatbot import mcp_bridge

    cid, mid = _seed(conv, store, **seed)
    if racing:
        monkeypatch.setattr(conv, "store", _RacingStore(store))
    calls, appended = [], []
    tool = SimpleNamespace(name="fake_write", safety="write", description="",
                           executor=lambda ctx, args: calls.append(dict(args)) or {"done": True})
    monkeypatch.setattr(mcp_bridge, "get_tool", lambda n: tool if n == "fake_write" else None)
    monkeypatch.setattr(conv, "append_messages", lambda c, u, cid_, msgs: appended.extend(msgs))

    def resolve(approved):
        return mcp_bridge.resolve_mcp_pending(conn=None, user={"id": "u1", "username": "op"},
                                              conversation_id=cid, message_id=mid, approved=approved)

    return SimpleNamespace(cid=cid, mid=mid, calls=calls, appended=appended,
                           approve=lambda: resolve(True), decline=lambda: resolve(False))


def test_two_concurrent_mcp_approvals_execute_once(conv, store, monkeypatch):
    m = _mcp(conv, store, monkeypatch, racing=True)
    outcomes = _race(m.approve, m.approve)
    assert m.calls == [{"qty": 1}]
    assert sorted(k for k, _ in outcomes) == ["err", "ok"]
    err = next(v for k, v in outcomes if k == "err")
    assert isinstance(err, ValueError) and "no pending tool" in str(err)
    final = conv.find_message(conv.get_conversation(None, "u1", m.cid), m.mid)
    assert final["status"] == "complete" and final["pending_tool"] is None
    assert [msg["role"] for msg in m.appended] == ["tool"]


def test_concurrent_mcp_approve_and_decline_apply_exactly_one(conv, store, monkeypatch):
    m = _mcp(conv, store, monkeypatch, racing=True)
    outcomes = _race(m.approve, m.decline)
    assert [k for k, _ in outcomes].count("ok") == 1
    declined = any("user_declined" in (msg.get("content") or "") for msg in m.appended)
    assert len(m.calls) + int(declined) == 1 and len(m.appended) == 1


def test_mcp_approval_of_an_executing_message_never_runs(conv, store, monkeypatch):
    m = _mcp(conv, store, monkeypatch, status="executing")
    with pytest.raises(ValueError, match="no pending tool"):
        m.approve()
    assert m.calls == []


def test_mcp_sequential_double_approve_runs_once(conv, store, monkeypatch):
    m = _mcp(conv, store, monkeypatch)
    m.approve()
    with pytest.raises(ValueError, match="no pending tool"):
        m.approve()
    assert len(m.calls) == 1


def test_mcp_result_is_kept_when_the_row_moved_on_while_the_tool_ran(conv, store, monkeypatch):
    """The tool ran, so its result is appended even when the claimed row is
    no longer "executing" by the time it finishes (here the thread was
    cleared mid-run)."""
    from chatbot import mcp_bridge

    m = _mcp(conv, store, monkeypatch)

    def clear_then_run(ctx, args):
        store.update(conv.TABLE, m.cid, {"messages": [], "message_count": 0})
        m.calls.append(dict(args))
        return {"done": True}

    monkeypatch.setattr(mcp_bridge, "get_tool", lambda n: SimpleNamespace(
        name=n, safety="write", description="", executor=clear_then_run))
    out = m.approve()
    assert m.calls == [{"qty": 1}]
    assert [msg["role"] for msg in out] == ["tool"] and m.appended == out


def test_mcp_wrong_source_is_rejected_before_any_claim(conv, store, monkeypatch):
    m = _mcp(conv, store, monkeypatch, source="orchestration")
    with pytest.raises(ValueError, match="wasn't produced by claude-cli"):
        m.approve()
    assert conv.find_message(conv.get_conversation(None, "u1", m.cid), m.mid)["status"] == "pending_confirmation"
    assert m.calls == []


# ── confirm_pending_tool: the /confirm-tool path claims the same way ────────


def _confirm(conv, store, monkeypatch, *, racing=False, source="orchestration", **seed):
    """Seed one fake_write approval and wire orchestration to a recording tool.

    ``calls`` collects the tool's arguments per run. _resume_after_tool_results
    is replaced by an echo of the messages it is handed, so no LLM runs and
    nothing is appended (append_messages is SQL). ``racing`` as in _mcp."""
    from chatbot import orchestration

    cid, mid = _seed(conv, store, source=source, **seed)
    if racing:
        monkeypatch.setattr(conv, "store", _RacingStore(store))
    calls = []
    tool = SimpleNamespace(name="fake_write", safety="write", description="",
                           executor=lambda ctx, args: calls.append(dict(args)) or {"done": True})
    monkeypatch.setattr(orchestration, "get_tool", lambda n: tool if n == "fake_write" else None)
    monkeypatch.setattr(orchestration, "_resume_after_tool_results",
                        lambda conn, user, conv_id, appended: list(appended))

    def confirm(approved):
        return orchestration.confirm_pending_tool(None, {"id": "u1", "username": "op"}, cid, mid, approved)

    return SimpleNamespace(cid=cid, mid=mid, calls=calls,
                           approve=lambda: confirm(True), decline=lambda: confirm(False))


def test_two_concurrent_confirm_tool_approvals_execute_once(conv, store, monkeypatch):
    m = _confirm(conv, store, monkeypatch, racing=True)
    outcomes = _race(m.approve, m.approve)
    assert m.calls == [{"qty": 1}]
    assert sorted(k for k, _ in outcomes) == ["err", "ok"]
    err = next(v for k, v in outcomes if k == "err")
    assert isinstance(err, ValueError) and "no pending tool found on that message" in str(err)
    winner = next(v for k, v in outcomes if k == "ok")
    assert [msg["role"] for msg in winner] == ["tool"]
    final = conv.find_message(conv.get_conversation(None, "u1", m.cid), m.mid)
    assert final["status"] == "complete" and final["pending_tool"] is None


def test_concurrent_confirm_tool_approve_and_decline_apply_exactly_one(conv, store, monkeypatch):
    m = _confirm(conv, store, monkeypatch, racing=True)
    outcomes = _race(m.approve, m.decline)
    assert [k for k, _ in outcomes].count("ok") == 1
    err = next(v for k, v in outcomes if k == "err")
    assert isinstance(err, ValueError) and "no pending tool found on that message" in str(err)
    winner = next(v for k, v in outcomes if k == "ok")
    declined = any("user_declined" in (msg.get("content") or "") for msg in winner)
    assert len(m.calls) + int(declined) == 1 and len(winner) == 1
    final = conv.find_message(conv.get_conversation(None, "u1", m.cid), m.mid)
    assert final["status"] == "complete" and final["pending_tool"] is None


def test_confirm_tool_on_an_executing_message_never_runs(conv, store, monkeypatch):
    m = _confirm(conv, store, monkeypatch, status="executing")
    with pytest.raises(ValueError, match="no pending tool found on that message"):
        m.approve()
    assert m.calls == []


def test_confirm_tool_final_message_keeps_its_tool_calls(conv, store, monkeypatch):
    m = _confirm(conv, store, monkeypatch)
    out = m.approve()
    assert m.calls == [{"qty": 1}] and [msg["role"] for msg in out] == ["tool"]
    final = conv.find_message(conv.get_conversation(None, "u1", m.cid), m.mid)
    assert final["status"] == "complete" and final["pending_tool"] is None
    assert final["tool_calls"] == [{"id": "tc-1", "name": "fake_write", "arguments": {"qty": 1}}]


def test_confirm_tool_decline_fills_in_missing_tool_calls(conv, store, monkeypatch):
    """A pending message saved without tool_calls is resolved with one built
    from its pending_tool, so the decline message that follows has a parent."""
    m = _confirm(conv, store, monkeypatch)
    msgs = conv.get_conversation(None, "u1", m.cid)["messages"]
    msgs[0]["tool_calls"] = None
    store.update(conv.TABLE, m.cid, {"messages": msgs})
    out = m.decline()
    assert m.calls == []
    assert [(msg["role"], msg["tool_call_id"]) for msg in out] == [("tool", "tc-1")]
    assert "user_declined" in out[0]["content"]
    final = conv.find_message(conv.get_conversation(None, "u1", m.cid), m.mid)
    assert final["status"] == "complete" and final["pending_tool"] is None
    assert final["tool_calls"] == [{"id": "tc-1", "name": "fake_write", "arguments": {"qty": 1}}]


@pytest.mark.parametrize("outer", ["confirm_tool", "mcp_confirm"])
def test_an_approval_on_the_other_endpoint_while_the_tool_runs_is_refused(conv, store, monkeypatch, outer):
    """/mcp-confirm and /confirm-tool claim through the same compare-and-swap,
    so an approval that lands on one endpoint while the other's tool is
    running is refused instead of running the tool a second time."""
    from chatbot import mcp_bridge, orchestration

    m = _mcp(conv, store, monkeypatch)
    monkeypatch.setattr(orchestration, "_resume_after_tool_results",
                        lambda conn, user, conv_id, appended: list(appended))
    via = {
        "mcp_confirm": m.approve,
        "confirm_tool": lambda: orchestration.confirm_pending_tool(
            None, {"id": "u1", "username": "op"}, m.cid, m.mid, True),
    }
    inner = next(fn for name, fn in via.items() if name != outer)
    inner_outcome = []

    def run_while_the_other_endpoint_approves(ctx, args):
        m.calls.append(dict(args))
        if len(m.calls) == 1:
            try:
                inner()
                inner_outcome.append("ran")
            except ValueError as e:
                inner_outcome.append(str(e))
        return {"done": True}

    tool = SimpleNamespace(name="fake_write", safety="write", description="",
                           executor=run_while_the_other_endpoint_approves)
    monkeypatch.setattr(mcp_bridge, "get_tool", lambda n: tool if n == "fake_write" else None)
    monkeypatch.setattr(orchestration, "get_tool", lambda n: tool if n == "fake_write" else None)
    via[outer]()
    assert m.calls == [{"qty": 1}]
    assert len(inner_outcome) == 1 and "no pending tool" in inner_outcome[0]
    final = conv.find_message(conv.get_conversation(None, "u1", m.cid), m.mid)
    assert final["status"] == "complete" and final["pending_tool"] is None


def test_llm_history_tolerates_a_stranded_executing_message():
    from chatbot import orchestration

    stranded = {"id": "a1", "role": "assistant", "content": "needs approval", "status": "executing",
                "tool_calls": [{"id": "tc-1", "name": "fake_write", "arguments": {}}]}
    out = orchestration._llm_messages_for_call([{"id": "u", "role": "user", "content": "hi"}, stranded], "sys")
    assert out == [{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"},
                   {"role": "assistant", "content": "needs approval"}]
