"""SwingSignals access, the decision state machine, and the order rebuild at
approval time (ST app.py api_approve / api_approve_wheel; fix 2)."""
import os
import sys
from datetime import date
from types import SimpleNamespace as NS

import pytest

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from swing_trader import approvals, signals_store  # noqa: E402

CFG = {"stop_loss": 0.06, "profit_target": 0.09, "position_size_pct": 0.125,
       "max_collateral_pct": 0.25, "target_delta": 0.25, "limit_bid_mult": 0.95}


@pytest.fixture
def ss(store, monkeypatch):
    monkeypatch.setattr(signals_store, "store", store)
    return store


def sig(symbol="AAPL", lane="swing", status="pending", created="2026-06-01T13:25:00+00:00",
        instance="swing-paper", session="2026-06-01", adj=0.5, proposal=None, score=62):
    return signals_store.new_signal(
        instance_id=instance, lane=lane, symbol=symbol, session=session, score=score,
        recommendation="review", reasoning="Clean pullback.", key_risks=["Earnings soon"],
        size_adjustment=adj, status=status, created_at=created,
        proposal=proposal or {"entry": 190.0, "stop": 178.6, "target": 207.1, "shares": 65})


def test_signal_ids_are_deterministic_uuid_hex():
    a = signals_store.signal_id_for("swing-paper", "swing", "2026-06-01", "aapl")
    assert a == signals_store.signal_id_for("swing-paper", "swing", "2026-06-01", "AAPL")
    assert len(a) == 32 and int(a, 16) >= 0
    assert a != signals_store.signal_id_for("swing-paper", "wheel", "2026-06-01", "AAPL")
    assert a != signals_store.signal_id_for("swing-paper", "swing", "2026-06-02", "AAPL")


def test_new_signal_is_the_contract_document():
    doc = sig()
    assert set(doc) >= {"id", "instance_id", "lane", "symbol", "session", "created_at",
                        "score", "recommendation", "reasoning", "key_risks",
                        "size_adjustment", "proposal", "status", "decided_by",
                        "decided_at", "decision_reason", "order_client_id", "outcome"}
    assert doc["recommendation"] == "REVIEW" and doc["outcome"] is None


def test_insert_keeps_the_first_row(ss):
    signals_store.insert_signal(sig())
    signals_store.insert_signal(sig(status="auto_approved"))
    assert signals_store.get_signal(sig()["id"])["status"] == "pending"
    assert signals_store.get_signal("nope") is None


def test_list_signals_filters_by_instance_and_status_newest_first(ss):
    signals_store.insert_signal(sig("AAA", created="2026-06-01T13:21:00+00:00"))
    signals_store.insert_signal(sig("BBB", created="2026-06-01T13:22:00+00:00"))
    signals_store.insert_signal(sig("CCC", status="ai_rejected",
                                    created="2026-06-01T13:23:00+00:00"))
    signals_store.insert_signal(sig("DDD", instance="other"))
    assert [r["symbol"] for r in signals_store.list_signals("swing-paper", "pending")] == ["BBB", "AAA"]
    assert [r["symbol"] for r in signals_store.list_signals("swing-paper")] == ["CCC", "BBB", "AAA"]
    assert len(signals_store.list_signals("swing-paper", limit=1)) == 1
    assert {r["symbol"] for r in signals_store.all_signals("swing-paper")} == {"AAA", "BBB", "CCC"}


def test_cas_signal_is_a_compare_and_swap(ss):
    doc = sig()
    signals_store.insert_signal(doc)
    assert signals_store.cas_signal(doc["id"], expect_status="pending",
                                    doc=dict(doc, status="approved")) is True
    assert signals_store.cas_signal(doc["id"], expect_status="pending",
                                    doc=dict(doc, status="rejected")) is False
    assert signals_store.get_signal(doc["id"])["status"] == "approved"


def test_update_signal_deep_merges(ss):
    doc = sig()
    signals_store.insert_signal(doc)
    signals_store.update_signal(doc["id"], {"outcome": {"pnl": 12.5}})
    signals_store.update_signal(doc["id"], {"outcome": {"pnl_pct": 1.2}})
    assert signals_store.get_signal(doc["id"])["outcome"] == {"pnl": 12.5, "pnl_pct": 1.2}


def test_swing_owned_symbols(ss):
    signals_store.insert_signal(sig("KR", status="auto_approved"))
    signals_store.insert_signal(sig("LITE", status="submitted"))
    signals_store.insert_signal(sig("GIS", status="pending"))
    closed = sig("PEP", status="auto_approved")
    closed["outcome"] = {"pnl": 1.0}
    signals_store.insert_signal(closed)
    signals_store.insert_signal(sig("APH", lane="wheel", status="auto_approved"))
    held = {"KR", "LITE", "GIS", "PEP", "APH", "XOM"}
    assert signals_store.swing_owned_symbols("swing-paper", held) == {"KR", "LITE"}
    assert signals_store.swing_owned_symbols("swing-paper", set()) == set()


def test_wheel_scans_round_trip(ss):
    base = {"instance_id": "swing-paper", "session": "2026-06-01", "symbol": "APH",
            "stock_price": 131.2, "strike": 127.8, "expiry": "2026-06-12",
            "premium_est": 1.7, "score": 81, "recommendation": "APPROVE",
            "reasoning": "r", "status": "placed", "skip_reason": None}
    first = signals_store.insert_wheel_scan(dict(base, created_at="2026-06-01T14:41:00+00:00"))
    again = signals_store.insert_wheel_scan(dict(base, created_at="2026-06-01T14:42:00+00:00",
                                                 status="skipped"))
    assert first == again                                  # a resumed scan replaces
    signals_store.insert_wheel_scan(dict(base, symbol="GIS", created_at="2026-06-01T14:43:00+00:00"))
    rows = signals_store.list_wheel_scans("swing-paper")
    assert [r["symbol"] for r in rows] == ["GIS", "APH"] and rows[1]["status"] == "skipped"
    assert len(signals_store.list_wheel_scans("swing-paper", limit=1)) == 1


# -- decide ------------------------------------------------------------------

def test_decide_statuses_and_audit_fields():
    now = "2026-06-01T14:00:00+00:00"
    for decision, status in (("approve", "approved"), ("approve_half", "approved_half"),
                             ("reject", "rejected")):
        out = approvals.decide(sig(), decision, "pranav", "  looks fine  ", now)
        assert out["status"] == status and out["decided_by"] == "pranav"
        assert out["decided_at"] == now and out["decision_reason"] == "looks fine"
    assert approvals.decide(sig(), "reject", "pranav", None, now)["decision_reason"] is None


def test_a_second_decision_conflicts():
    first = approvals.decide(sig(), "approve", "pranav", None, "t1")
    for status in ("approved", "approved_half", "rejected", "submitted", "failed",
                   "auto_approved", "ai_rejected"):
        with pytest.raises(approvals.SignalConflict):
            approvals.decide(dict(first, status=status), "reject", "pranav", None, "t2")
    assert issubclass(approvals.SignalConflict, ValueError)


def test_an_unknown_decision_is_a_value_error():
    with pytest.raises(ValueError, match="decision must be one of"):
        approvals.decide(sig(), "maybe", "pranav", None, "t")


# -- build_approved_order: swing (app.py:1004-1045) ---------------------------

def test_the_swing_order_is_rebuilt_at_the_live_price():
    s = dict(sig(), status="approved")
    out = approvals.build_approved_order(s, live_price=100.0, equity=100_000.0, cfg=CFG)
    # base = int(12_500 / 100) = 125; x0.5 adjustment = 62 shares
    assert out == {"kind": "equity_bracket", "symbol": "AAPL", "qty": 62,
                   "take_profit_price": 109.0, "stop_loss_price": 94.0}


def test_approve_half_halves_the_adjusted_shares():
    s = dict(sig(), status="approved_half")
    assert approvals.build_approved_order(s, live_price=100.0, equity=100_000.0,
                                          cfg=CFG)["qty"] == 31


def test_a_price_too_small_for_the_brackets_fails_the_sanity_check():
    s = dict(sig(), status="approved")
    with pytest.raises(ValueError, match="after recalc"):
        approvals.build_approved_order(s, live_price=0.1, equity=100_000.0, cfg=CFG)
    with pytest.raises(ValueError):
        approvals.build_approved_order(s, live_price=0.0, equity=100_000.0, cfg=CFG)


# -- build_approved_order: wheel (app.py:888-940, fix 2) ----------------------

def contract(strike, expiry):
    ymd = expiry[2:4] + expiry[5:7] + expiry[8:10]
    return NS(symbol=f"APH{ymd}P{int(strike * 1000):08d}", underlying="APH",
              option_type="put", strike=strike, expiration=expiry,
              open_interest=10, close_price=1.9)


class WheelAdapter:
    def __init__(self, positions=()):
        self.positions = list(positions)
        self.chain = [contract(125.0, "2026-05-29"), contract(125.0, "2026-06-12"),
                      contract(128.0, "2026-06-12")]

    def get_option_contracts(self, underlying, **kw):
        return [c for c in self.chain if kw["expiration_gte"] <= c.expiration <= kw["expiration_lte"]]

    def get_option_snapshots(self, symbols):
        return {s: NS(bid=1.8, delta=(-0.25 if "00128000" in s else -0.15)) for s in symbols}

    def get_account_options(self):
        return {"cash": 200_000.0, "equity": 200_000.0}

    def list_option_positions(self):
        return list(self.positions)

    def list_open_orders(self, limit=200):
        return []


def wheel_sig(status="approved", qty=2):
    return signals_store.new_signal(
        instance_id="swing-paper", lane="wheel", symbol="APH", session="2026-05-18",
        score=66, recommendation="review", reasoning="r", key_risks=[], size_adjustment=None,
        status=status, created_at="2026-05-18T14:41:00+00:00",
        proposal={"contract": None, "strike": 127.8, "expiry": "2026-05-29", "qty": qty,
                  "limit_price": None, "premium_est": 1.7, "delta": None})


def test_fix_2_the_wheel_expiry_is_recomputed_at_approval():
    out = approvals.build_approved_order(wheel_sig(), live_price=131.2, equity=200_000.0,
                                         cfg=CFG, adapter=WheelAdapter(),
                                         today=date(2026, 6, 1))
    assert out["kind"] == "option" and out["expiry"] == "2026-06-12"
    assert out["contract"] == "APH260612P00128000" and out["strike"] == 128.0
    assert out["position_intent"] == "sell_to_open" and out["qty"] == 2
    assert out["limit_price"] == round(1.8 * 0.95, 2)
    half = approvals.build_approved_order(wheel_sig("approved_half"), live_price=131.2,
                                          equity=200_000.0, cfg=CFG, adapter=WheelAdapter(),
                                          today=date(2026, 6, 1))
    assert half["qty"] == 1


def test_a_wheel_approval_refuses_a_duplicate_put_and_needs_the_adapter():
    held = NS(symbol="APH260605P00120000", underlying="APH", option_type="put",
              strike=120.0, expiry="2026-06-05", qty=-1, avg_entry_price=1.0,
              market_value=-100.0)
    with pytest.raises(ValueError, match="duplicate"):
        approvals.build_approved_order(wheel_sig(), live_price=131.2, equity=200_000.0,
                                       cfg=CFG, adapter=WheelAdapter([held]),
                                       today=date(2026, 6, 1))
    with pytest.raises(ValueError, match="adapter"):
        approvals.build_approved_order(wheel_sig(), live_price=131.2, equity=200_000.0,
                                       cfg=CFG)


def test_an_unknown_lane_raises():
    with pytest.raises(ValueError, match="lane"):
        approvals.build_approved_order(dict(sig(), lane="crypto"), live_price=1.0,
                                       equity=1.0, cfg=CFG)


class UnreadableBookAdapter(WheelAdapter):
    """Alpaca's lenient list_open_orders answers an outage with []; the strict
    reader raises. The approval must read the strict one and refuse."""

    def list_open_orders_strict(self, limit=200):
        raise RuntimeError("orders endpoint unreachable")


def test_f3_an_unreadable_order_book_refuses_the_wheel_approval():
    with pytest.raises(approvals.BookUnreadable, match="unreadable"):
        approvals.build_approved_order(wheel_sig(), live_price=131.2, equity=200_000.0,
                                       cfg=CFG, adapter=UnreadableBookAdapter(),
                                       today=date(2026, 6, 1))
    assert issubclass(approvals.BookUnreadable, ValueError)


def test_f3_a_working_put_on_the_strict_book_is_a_duplicate():
    working = NS(symbol="APH260612P00125000", side="sell", qty=1, filled_qty=0,
                 position_intent="sell_to_open")

    class StrictBook(WheelAdapter):
        def list_open_orders(self, limit=200):
            return []                                    # the lenient answer

        def list_open_orders_strict(self, limit=200):
            return [working]

    with pytest.raises(ValueError, match="duplicate: working sell order"):
        approvals.build_approved_order(wheel_sig(), live_price=131.2, equity=200_000.0,
                                       cfg=CFG, adapter=StrictBook(),
                                       today=date(2026, 6, 1))


# -- G8a ruling 2 (G7 carry b): the wheel approval reads the option map through
# -- account.option_book and fails closed ---------------------------------------

class OptionMapAdapter(WheelAdapter):
    """AlpacaAdapter.list_option_positions never raises: after an outage, or
    while a contract's fields are unreadable, it returns a PARTIAL map and says
    so only through its private health flags."""

    def __init__(self, positions=(), *, complete=True, stale_since=None, down=False):
        super().__init__(positions)
        self._option_positions_complete = complete
        self._positions_stale_since = stale_since
        self.down = down

    def list_option_positions(self):
        if self.down:
            raise RuntimeError("positions endpoint unreachable")
        return list(self.positions)


@pytest.mark.parametrize("adapter,needle", [
    # The held APH put is missing from the partial map: read as complete, a
    # second put would be sold on APH.
    (OptionMapAdapter([], complete=False), "incomplete"),
    (OptionMapAdapter([], stale_since=1.0), "stale"),
    (OptionMapAdapter([], down=True), "unreachable"),
])
def test_g8a_an_unready_option_map_refuses_the_wheel_approval(adapter, needle):
    with pytest.raises(approvals.BookUnreadable, match=needle):
        approvals.build_approved_order(wheel_sig(), live_price=131.2, equity=200_000.0,
                                       cfg=CFG, adapter=adapter, today=date(2026, 6, 1))


def test_g8a_a_complete_current_option_map_still_builds_the_order():
    out = approvals.build_approved_order(wheel_sig(), live_price=131.2, equity=200_000.0,
                                         cfg=CFG, adapter=OptionMapAdapter([]),
                                         today=date(2026, 6, 1))
    assert out["contract"] == "APH260612P00128000"
