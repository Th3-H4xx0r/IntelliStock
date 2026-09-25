"""Score-vs-outcome calibration (ST calibration.py) over SwingSignals, and the
outcome records the lanes write from the broker's closed orders."""
import os
import sys
from types import SimpleNamespace as NS

import pytest

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from swing_trader import calibration as cal, signals_store  # noqa: E402


def swing_row(symbol, score, buy, sell, shares):
    pnl = round((sell - buy) * shares, 2)
    return {"lane": "swing", "symbol": symbol, "score": score, "status": "auto_approved",
            "outcome": {"entry_price": buy, "exit_price": sell, "shares": shares,
                        "pnl": pnl, "pnl_pct": round((sell - buy) / buy * 100, 4)}}


def test_swing_buckets_round_trips_by_score():
    result = cal.swing_calibration([swing_row("AAPL", 80, 100.0, 110.0, 10),
                                    swing_row("MSFT", 55, 200.0, 190.0, 5)])
    assert result["closed_total"] == 2 and result["closed_scored"] == 2
    b75 = result["buckets"]["75-84"]
    assert (b75["count"], b75["win_rate"], b75["total_pnl"]) == (1, 100.0, 100.0)
    assert result["buckets"]["50-64"]["win_rate"] == 0.0


def test_swing_excludes_blank_scores_not_zero():
    result = cal.swing_calibration([swing_row("AAPL", None, 100.0, 110.0, 10)])
    assert (result["closed_total"], result["closed_scored"], result["excluded_unscored"]) == (1, 0, 1)
    assert all(b["count"] == 0 for b in result["buckets"].values())


def test_swing_insufficient_n_labeling():
    rows = [swing_row(f"S{i}", 90, 100.0, 105.0, 1) for i in range(6)]
    result = cal.swing_calibration(rows)
    assert result["buckets"]["85-100"]["insufficient_n"] is False
    assert result["buckets"]["50-64"]["insufficient_n"] is True


def test_open_rows_are_not_round_trips():
    open_row = dict(swing_row("AAPL", 80, 100.0, 110.0, 10), outcome=None)
    assert cal.swing_calibration([open_row])["closed_total"] == 0


def wheel_row(symbol, score, premium, contracts):
    return {"lane": "wheel", "symbol": symbol, "score": score, "status": "auto_approved",
            "outcome": None if premium is None else {"premium_received": premium,
                                                     "contracts": contracts}}


def test_wheel_buckets_placed_orders():
    result = cal.wheel_calibration([wheel_row("AAPL", 72, 2.50, 1),
                                    wheel_row("MSFT", 55, 1.00, 2),
                                    wheel_row("NVDA", 62, None, 1)])
    assert result["placed_total"] == 2
    assert result["buckets"]["65-74"]["total_premium"] == 250.0
    assert result["buckets"]["50-64"]["total_premium"] == 200.0


def test_wheel_with_no_rows_is_empty():
    assert cal.wheel_calibration([]) == {"placed_total": 0, "buckets": {}}


def test_report_gate():
    report = cal.calibration_report(rows=[swing_row("AAPL", 80, 100.0, 110.0, 10)])
    assert report["gate"] == {"closed_scored_trades": 1, "required": 20, "met": False}
    assert set(report) == {"swing", "wheel", "gate"}


def test_score_of_parses_like_st():
    assert [cal._score_of(v) for v in (None, "", "  ", "72", 72.9, "x", float("nan"))] == [
        None, None, None, 72, 72, None, None]


# -- outcomes -----------------------------------------------------------------

def order(symbol, side, price, qty=10, status="filled", at="2026-06-02T13:30:00+00:00",
          legs=(), oid="o"):
    return NS(broker_order_id=oid, client_order_id=oid, symbol=symbol, side=side,
              qty=qty, status=status, filled_qty=qty if status == "filled" else 0,
              filled_avg_price=price if status == "filled" else None,
              submitted_at_utc=at, legs=legs)


def test_a_bracket_round_trip_resolves_to_the_fill_prices():
    tp = order("AAPL", "sell", 109.2, at="2026-06-02T13:30:00+00:00", oid="tp")
    sl = order("AAPL", "sell", None, status="canceled", oid="sl")
    parent = order("AAPL", "buy", 100.4, legs=(tp, sl), oid="parent")
    signal = {"symbol": "AAPL", "created_at": "2026-06-01T13:25:00+00:00"}
    out = cal.resolve_swing_outcome(signal, [parent])
    assert (out["entry_price"], out["exit_price"], out["shares"]) == (100.4, 109.2, 10)
    assert out["pnl"] == 88.0 and out["exit_order_id"] == "tp"
    assert cal.resolve_swing_outcome(signal, [order("AAPL", "buy", 100.4)]) is None


def test_a_wheel_fill_records_the_premium():
    signal = {"lane": "wheel", "proposal": {"contract": "APH260612P00128000"}}
    fill = order("APH260612P00128000", "sell", 1.71, qty=2)
    assert cal.resolve_wheel_outcome(signal, [fill]) == {
        "premium_received": 1.71, "contracts": 2, "order_id": "o"}
    assert cal.resolve_wheel_outcome({"proposal": {}}, [fill]) is None


def test_record_outcomes_updates_closed_rows_only(store, monkeypatch):
    monkeypatch.setattr(signals_store, "store", store)
    for symbol, status in (("AAPL", "auto_approved"), ("MSFT", "auto_approved"),
                           ("GIS", "pending")):
        signals_store.insert_signal(signals_store.new_signal(
            instance_id="swing-paper", lane="swing", symbol=symbol, session="2026-06-01",
            score=80, recommendation="approve", reasoning="", key_risks=[],
            size_adjustment=1.0, proposal={}, status=status,
            created_at="2026-06-01T13:25:00+00:00"))

    class Adapter:
        def list_closed_orders(self, symbols, after):
            self.args = (sorted(symbols), after)
            return [order("AAPL", "buy", 100.0, legs=(order("AAPL", "sell", 110.0),))]

    a = Adapter()
    n = cal.record_outcomes("swing-paper", a, "swing", held={"MSFT"})
    assert n == 1 and a.args == (["AAPL"], "2026-06-01")
    rows = {r["symbol"]: r for r in signals_store.all_signals("swing-paper")}
    assert rows["AAPL"]["outcome"]["pnl"] == 100.0
    assert rows["MSFT"]["outcome"] is None and rows["GIS"]["outcome"] is None


def test_record_outcomes_survives_an_adapter_without_the_method(store, monkeypatch):
    monkeypatch.setattr(signals_store, "store", store)
    signals_store.insert_signal(signals_store.new_signal(
        instance_id="swing-paper", lane="swing", symbol="AAPL", session="2026-06-01",
        score=80, recommendation="approve", reasoning="", key_risks=[], size_adjustment=1.0,
        proposal={}, status="auto_approved"))
    assert cal.record_outcomes("swing-paper", object(), "swing", held=set()) == 0


# -- G8a ruling 4 (G5 minor 2): a swing entry that never fills closes ----------

from datetime import date  # noqa: E402


def _open_swing(symbol, session, *, created_at=None, status="auto_approved"):
    doc = signals_store.new_signal(
        instance_id="swing-paper", lane="swing", symbol=symbol, session=session, score=80,
        recommendation="approve", reasoning="", key_risks=[], size_adjustment=1.0,
        proposal={}, status=status, created_at=created_at or f"{session}T13:25:00+00:00")
    signals_store.insert_signal(doc)
    return doc["id"]


class ClosedOrders:
    def __init__(self, orders=()):
        self.orders, self.calls = list(orders), []

    def list_closed_orders(self, symbols, after):
        self.calls.append((sorted(symbols), after))
        return [o for o in self.orders if o.symbol in symbols]


def test_an_entry_unfilled_after_two_sessions_is_closed_as_unfilled(store, monkeypatch):
    monkeypatch.setattr(signals_store, "store", store)
    # Thursday 2026-07-02; Friday 07-03 is the observed Independence Day holiday.
    never = _open_swing("NVR", "2026-07-02")
    filled = _open_swing("FIL", "2026-07-02")            # bought, exit not yet visible
    submitted = _open_swing("SUB", "2026-07-02", status="submitted")
    held = _open_swing("HLD", "2026-07-02")
    adapter = ClosedOrders([order("FIL", "buy", 50.0, at="2026-07-02T13:31:00+00:00")])

    # One session after (Monday 07-06): still inside the window.
    assert cal.record_outcomes("swing-paper", adapter, "swing", held={"HLD"}, working=set(),
                               today=date(2026, 7, 6)) == 0
    assert signals_store.get_signal(never)["outcome"] is None

    # Two sessions after (Tuesday 07-07): the entries with no fill close.
    assert cal.record_outcomes("swing-paper", adapter, "swing", held={"HLD"}, working=set(),
                               today=date(2026, 7, 7)) == 2
    for sid in (never, submitted):
        row = signals_store.get_signal(sid)
        assert row["outcome"]["unfilled"] is True
        assert row["status"] in ("auto_approved", "submitted")      # status kept
    assert signals_store.get_signal(filled)["outcome"] is None      # it did fill
    assert signals_store.get_signal(held)["outcome"] is None        # still held

    # Closed: it no longer marks the symbol swing-owned, and it no longer pins
    # the closed-orders window for later signals.
    assert signals_store.swing_owned_symbols("swing-paper", {"NVR", "HLD"}) == {"HLD"}
    _open_swing("NEW", "2026-07-08")
    cal.record_outcomes("swing-paper", adapter, "swing", held={"HLD", "FIL"},
                        working=set(), today=date(2026, 7, 8))
    assert adapter.calls[-1] == (["NEW"], "2026-07-08")
    # An unfilled close never counts as a round trip.
    report = cal.calibration_report("swing-paper")
    assert report["swing"]["closed_total"] == 0


def test_an_unfilled_close_needs_a_positions_read(store, monkeypatch):
    # Without `held` the lane cannot say the name is not held, so nothing is
    # closed as unfilled; nor is a wheel signal.
    monkeypatch.setattr(signals_store, "store", store)
    sid = _open_swing("NVR", "2026-07-02")
    assert cal.record_outcomes("swing-paper", ClosedOrders(), "swing",
                               today=date(2026, 7, 9)) == 0
    assert signals_store.get_signal(sid)["outcome"] is None
    wheel = signals_store.new_signal(
        instance_id="swing-paper", lane="wheel", symbol="APH", session="2026-07-02",
        score=80, recommendation="approve", reasoning="", key_risks=[],
        size_adjustment=None, proposal={"contract": "APH260710P00128000"},
        status="auto_approved")
    signals_store.insert_signal(wheel)
    assert cal.record_outcomes("swing-paper", ClosedOrders(), "wheel",
                               today=date(2026, 7, 9)) == 0
    assert signals_store.get_signal(wheel["id"])["outcome"] is None


def test_a_closed_orders_outage_closes_nothing(store, monkeypatch):
    monkeypatch.setattr(signals_store, "store", store)
    sid = _open_swing("NVR", "2026-07-02")

    class Down:
        def list_closed_orders(self, symbols, after):
            raise RuntimeError("orders endpoint down")

    assert cal.record_outcomes("swing-paper", Down(), "swing", held=set(), working=set(),
                               today=date(2026, 7, 9)) == 0
    assert signals_store.get_signal(sid)["outcome"] is None


# -- G8a fix round 1, M4: a GTC entry still working at the broker is not unfilled --

def test_a_still_working_entry_is_never_closed_as_unfilled(store, monkeypatch):
    # A-live places swing entries as GTC brackets: one halted for days can
    # still fill, so a working buy keeps the signal open.
    monkeypatch.setattr(signals_store, "store", store)
    sid = _open_swing("NVR", "2026-07-02")
    assert cal.record_outcomes("swing-paper", ClosedOrders(), "swing", held=set(),
                               working={"NVR"}, today=date(2026, 7, 9)) == 0
    assert signals_store.get_signal(sid)["outcome"] is None
    # Without the lane's working-order read nothing is closed (fail closed).
    assert cal.record_outcomes("swing-paper", ClosedOrders(), "swing", held=set(),
                               today=date(2026, 7, 9)) == 0
    assert signals_store.get_signal(sid)["outcome"] is None
    assert cal.record_outcomes("swing-paper", ClosedOrders(), "swing", held=set(),
                               working=set(), today=date(2026, 7, 9)) == 1
    assert signals_store.get_signal(sid)["outcome"]["unfilled"] is True
