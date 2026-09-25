"""Position rules: ST check_open_wheel_positions (2x premium), app.py
api_check_wheel_positions (the 15:45 monitor), and check_assigned_positions
(ST tests/test_assignment_detection.py, ported to the pure detector)."""
import os
import sys
from datetime import date
from types import SimpleNamespace as NS

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from swing_trader import wheel_rules as wr  # noqa: E402

TODAY = date(2026, 6, 1)


def put(symbol="Q260612P00100000", qty=-1, avg=1.0, mv=-150.0, kind="put",
        underlying="Q", strike=100.0, expiry="2026-06-12"):
    return NS(symbol=symbol, underlying=underlying, option_type=kind, strike=strike,
              expiry=expiry, qty=qty, avg_entry_price=avg, current_price=None,
              market_value=mv, unrealized_pl=None, multiplier=100)


def test_two_x_premium_buys_back_a_short_put():
    exits = wr.two_x_exits([put(mv=-210.0)])
    assert len(exits) == 1
    order, info = exits[0]
    assert order["position_intent"] == "buy_to_close" and order["order_type"] == "market"
    assert order["qty"] == 1 and order["reason"] == "wheel_btc_2x"
    assert info["loss_ratio"] == 2.1


def test_two_x_ignores_calls_longs_small_losses_and_working_closes():
    assert wr.two_x_exits([put(kind="call", mv=-500.0)]) == []       # fix 6
    assert wr.two_x_exits([put(qty=2, mv=500.0)]) == []
    assert wr.two_x_exits([put(mv=-150.0)]) == []
    working = NS(symbol="Q260612P00100000", side="buy")
    assert wr.two_x_exits([put(mv=-300.0)], open_orders=[working]) == []


def decide(stock, *, expiry="2026-06-12", strike=100.0):
    return wr.put_monitor_decision(contract="Q260612P00100000", underlying="Q",
                                   strike=strike, expiry=expiry, stock_price=stock,
                                   today=TODAY)


def test_monitor_auto_closes_ten_percent_itm_at_any_dte():
    d = decide(88.0)
    assert d["action"] == "auto_close" and d["intent"] == "wheel_btc_itm"
    assert d["priority"] == 2 and round(d["itm_pct"], 1) == 12.0


def test_monitor_auto_closes_five_percent_itm_inside_two_days():
    d = decide(94.0, expiry="2026-06-03")
    assert d["action"] == "auto_close" and d["dte"] == 2


def test_monitor_auto_closes_any_itm_on_expiry_day():
    d = decide(99.5, expiry="2026-06-01")
    assert d["action"] == "auto_close" and d["intent"] == "wheel_btc_expiry"


def test_monitor_alerts_below_the_thresholds_and_informs_on_otm_expiry():
    assert decide(98.0)["action"] == "alert_itm"
    assert decide(98.0)["priority"] == 1
    tomorrow = decide(105.0, expiry="2026-06-02")
    assert tomorrow["action"] == "info_expiring" and "TOMORROW" in tomorrow["title"]
    assert "TODAY" in decide(105.0, expiry="2026-06-01")["title"]
    assert decide(105.0)["action"] is None


def test_monitor_without_a_price_does_nothing_and_says_why():
    d = decide(None, expiry="2026-06-01")
    assert d["action"] is None and d["reason"] == "no underlying price"


# -- ST tests/test_assignment_detection.py, on the detector ------------------

def equity(qty=100, cost=790.0):
    return {"qty": qty, "avg_entry_price": cost}


def test_detection_fires_on_100_shares_with_nothing_covering():
    cands = wr.covered_call_candidates({"LITE": equity()}, [], [])
    assert cands == [{"symbol": "LITE", "qty": 100, "cost_basis": 790.0,
                      "call_strike": 829.5, "n_contracts": 1}]
    title, msg = wr.dry_run_message(cands[0], "2026-06-12")
    assert "dry-run" in title.lower() and "NO order placed" in msg


def test_existing_short_call_order_suppresses_detection():
    open_call = NS(symbol="LITE260626C00830000", side="sell")
    assert wr.covered_call_candidates({"LITE": equity()}, [], [open_call]) == []


def test_under_100_shares_skipped():
    assert wr.covered_call_candidates({"LITE": equity(qty=99)}, [], []) == []


def test_open_swing_position_never_gets_covered_call():
    assert wr.covered_call_candidates({"KR": equity(250, 60.0)}, [], [],
                                      swing_owned={"KR"}) == []


def test_held_short_call_position_suppresses_detection():
    held = put(symbol="LITE260626C00830000", kind="call", underlying="LITE", strike=830.0)
    assert wr.covered_call_candidates({"LITE": equity()}, [held], []) == []


def test_two_hundred_shares_is_two_contracts_and_the_strike_pick():
    cands = wr.covered_call_candidates({"LITE": equity(qty=200)}, [], [])
    assert cands[0]["n_contracts"] == 2
    calls = [NS(symbol="LITE260626C00820000", strike=820.0, close_price=15.0),
             NS(symbol="LITE260626C00830000", strike=830.0, close_price=12.0),
             NS(symbol="LITE260626C00840000", strike=840.0, close_price=9.0)]
    best, strike, limit = wr.pick_covered_call(calls, 829.5, 790.0)
    assert (best.symbol, strike, limit) == ("LITE260626C00830000", 830.0, 12.0)
    best, strike, limit = wr.pick_covered_call(calls[:1], 829.5, 790.0)
    assert strike == 820.0          # no strike at/above: ST uses the lowest available
    no_close = [NS(symbol="X", strike=900.0, close_price=None)]
    assert wr.pick_covered_call(no_close, 829.5, 790.0)[2] == 7.9
    assert wr.pick_covered_call([], 829.5, 790.0) is None


# -- contract §9 item 3: itm_pct is signed, and None without a price ----------

def test_itm_pct_is_signed_and_none_without_a_price():
    otm = decide(105.0)
    assert otm["itm_pct"] == -5.0 and otm["itm"] is False and otm["deep_itm"] is False
    itm = decide(97.0)
    assert round(itm["itm_pct"], 6) == 3.0 and itm["deep_itm"] is True
    none = decide(None)
    assert none["itm_pct"] is None and none["itm"] is False and none["deep_itm"] is False


def test_a_zero_or_unusable_price_is_no_price_not_a_full_itm_put():
    # Signed, a $0 print would read 100% ITM and auto-close at market; ST's
    # `if itm and stock_price` never let a zero reach the thresholds.
    for bad in (0.0, -1.0, float("nan"), float("inf")):
        d = decide(bad, expiry="2026-06-01")
        assert d["action"] is None and d["reason"] == "no underlying price", bad
        assert d["itm_pct"] is None and d["stock_price"] is None and d["itm"] is False


def test_two_x_skips_a_short_put_with_no_market_value():
    assert wr.two_x_exits([put(mv=None)]) == []
