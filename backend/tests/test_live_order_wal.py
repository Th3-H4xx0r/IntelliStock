"""LiveOrderWAL state machine tests."""
import os
import sys

_backend = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from broker_adapters._wal import LiveOrderWAL, InMemoryStore, TERMINAL_STATES


def _w() -> LiveOrderWAL:
    return LiveOrderWAL(InMemoryStore())


def test_record_and_get():
    w = _w()
    w.record_intent("c1", "AAPL", "buy", qty=1.0, notional=None)
    r = w.get("c1")
    assert r is not None
    assert r.client_order_id == "c1"
    assert r.state == "intent"
    assert r.symbol == "AAPL"


def test_submit_and_fill_transitions():
    w = _w()
    w.record_intent("c2", "AAPL", "buy", qty=1.0, notional=None)
    w.mark_submitted("c2", broker_order_id="b1")
    w.mark_filled("c2", filled_qty=1.0, filled_avg_price=100.5)
    r = w.get("c2")
    assert r.state == "filled"
    assert r.broker_order_id == "b1"
    assert r.filled_qty == 1.0
    assert r.filled_avg_price == 100.5


def test_partial_fill_then_filled():
    w = _w()
    w.record_intent("c3", "AAPL", "buy", qty=10.0, notional=None)
    w.mark_submitted("c3", "b3")
    w.mark_partial("c3", filled_qty=4.0, filled_avg_price=100.0)
    assert w.get("c3").state == "partial"
    w.mark_filled("c3", filled_qty=10.0, filled_avg_price=100.25)
    assert w.get("c3").state == "filled"
    assert w.get("c3").filled_qty == 10.0


def test_rejected_records_reason():
    w = _w()
    w.record_intent("c4", "XYZ", "buy", qty=1.0, notional=None)
    w.mark_rejected("c4", "insufficient buying power")
    r = w.get("c4")
    assert r.state == "rejected"
    assert "insufficient" in (r.reject_reason or "")


def test_list_open_excludes_terminal():
    w = _w()
    w.record_intent("a", "X", "buy", qty=1.0, notional=None)
    w.record_intent("b", "Y", "buy", qty=1.0, notional=None)
    w.mark_filled("a", filled_qty=1.0, filled_avg_price=1.0)
    open_cids = {r.client_order_id for r in w.list_open()}
    assert open_cids == {"b"}


def test_all_terminal_states_accounted():
    w = _w()
    w.record_intent("x1", "X", "buy", qty=1.0, notional=None)
    w.mark_filled("x1", 1.0, 1.0)
    w.record_intent("x2", "X", "buy", qty=1.0, notional=None)
    w.mark_canceled("x2")
    w.record_intent("x3", "X", "buy", qty=1.0, notional=None)
    w.mark_rejected("x3", "r")
    w.record_intent("x4", "X", "buy", qty=1.0, notional=None)
    w.mark_expired("x4")
    assert len(w.list_open()) == 0
    for cid, expected in [("x1", "filled"), ("x2", "canceled"), ("x3", "rejected"), ("x4", "expired")]:
        assert w.get(cid).state == expected
        assert expected in TERMINAL_STATES
