"""Tests for the feedback loop (reconcile) + the new selection-gate behavior."""
from kalshi.reconcile import (
    validate_order, aggregate_positions, reconcile_position,
    calibration_summary, go_live_ready,
)
from kalshi.strategy.candidates import generate_candidates


# --- order validation ---

def test_validate_order_bounds():
    assert validate_order(5, 50)[0] is True
    assert validate_order(0, 50)[0] is False          # contracts < 1
    assert validate_order(5, 0)[0] is False            # price < 1
    assert validate_order(5, 100)[0] is False          # price > 99
    assert validate_order("x", 50)[0] is False         # non-integer


# --- B2 fix: multiple placed rows for one ticker count ONCE ---

def _placed(ticker, size, fair, edge, inst="i1"):
    return {"decision": "placed", "instance_id": inst, "market_ticker": ticker,
            "id": f"{inst}|{ticker}|{size}", "size": size, "fused_fair": fair, "edge": edge}


def test_aggregate_positions_collapses_reentries():
    rows = [_placed("KX-A", 10, 0.60, 0.10), _placed("KX-A", 20, 0.62, 0.12),
            _placed("KX-A", 30, 0.58, 0.08)]
    pos = aggregate_positions(rows)
    assert len(pos) == 1                                # one net position, not three
    p = pos[0]
    assert p["contracts"] == 60                         # summed
    assert len(p["decision_ids"]) == 3


def test_reconcile_position_pnl_and_clv():
    # bought 60 contracts avg ~50c; YES wins -> ~+50c/contract minus fee
    pos = {"instance_id": "i1", "market_ticker": "KX-A", "contracts": 60,
           "avg_entry_cents": 50.0, "cost_cents": 3000.0, "decision_ids": ["a"]}
    win = reconcile_position(pos, result="yes")
    assert win["outcome"] == "win"
    assert win["realized_pnl_cents"] > 0
    loss = reconcile_position(pos, result="no")
    assert loss["outcome"] == "loss"
    assert loss["realized_pnl_cents"] < 0
    # CLV is only a real grade when measured vs a sharp book
    graded = reconcile_position(pos, result="yes", sharp_close_prob=0.60)
    assert graded["clv_graded"] is True
    assert graded["clv"] > 0                            # entered 50c, sharp closed 60c -> +CLV


def test_go_live_gate_is_no_go_by_default():
    # too few graded bets -> NOT ready
    assert go_live_ready([], min_graded=100)["ready"] is False
    # a positive but noisy point estimate must NOT pass (lower-bound test)
    noisy = [{"clv": 0.5, "clv_graded": True}, {"clv": -0.45, "clv_graded": True}] * 60
    res = go_live_ready(noisy, min_graded=100, clv_threshold=0.0)
    assert res["ready"] is False                        # mean ~0.025 but LCB < 0


# --- selection gate: price band + draw rule ---

def _winner_markets(price):
    return [{"market_ticker": "KX-HOME", "market_type": "winner", "side": "home", "yes_ask_cents": price}]


def test_price_band_rejects_cheap_longshots():
    probs = {"winner": {"home": 0.50}}                  # huge edge on a 8c longshot
    cands = generate_candidates("f1", "low", probs, _winner_markets(8),
                                fee_rate=0.07, edge_threshold=0.04)
    assert cands == []                                  # < 15c -> rejected despite big edge


def test_collect_skips_records_rejections():
    # a market below the edge bar is returned as a skip WITH a reason -> decision log
    probs = {"winner": {"home": 0.55}}
    markets = [{"market_ticker": "KX-HOME", "market_type": "winner", "side": "home", "yes_ask_cents": 54}]
    cands, skips = generate_candidates("f1", "low", probs, markets,
                                       fee_rate=0.07, edge_threshold=0.04, collect_skips=True)
    assert cands == []
    assert len(skips) == 1 and skips[0]["market_ticker"] == "KX-HOME" and "edge" in skips[0]["reason"]


def test_cheap_side_cap_curbs_overconfidence():
    from kalshi.intelligence.fusion import fuse, cheap_side_cap
    # model wildly overconfident (0.40) on a cheap 0.05 sharp longshot -> no overshoot
    f = fuse(sharp=0.05, model=0.40, llm_adjustment=0.0, w_sharp=0.85, llm_cap=0.05)
    assert f <= 0.05 + 1e-9
    # mid outcome allows a small price-scaled margin above sharp
    g = fuse(sharp=0.30, model=0.60, llm_adjustment=0.0, w_sharp=0.85, llm_cap=0.05)
    assert g <= 0.30 + cheap_side_cap(0.30) + 1e-9


def test_draw_requires_larger_edge():
    markets = [{"market_ticker": "KX-TIE", "market_type": "winner", "side": "draw", "yes_ask_cents": 25}]
    # a ~6% edge clears the normal 0.04 bar but NOT the 0.10 draw bar
    probs = {"winner": {"draw": 0.33}}
    cands = generate_candidates("f1", "low", probs, markets,
                                fee_rate=0.07, edge_threshold=0.04, draw_min_edge=0.10)
    assert cands == []


# --- orderbook parser + maker placement (Kalshi returns BIDS only) ---

def test_orderbook_parse_and_maker_price():
    from kalshi.live import orderbook as ob
    raw = {"orderbook_fp": {"yes_dollars": [["0.40", "30"], ["0.42", "10"]],
                            "no_dollars": [["0.55", "20"], ["0.50", "8"]]}}
    b = ob.parse(raw)
    assert b.top_bid == 42                       # highest YES bid
    assert b.top_ask == 45                       # 100 - highest NO bid (55)
    assert b.bid_depth == 10 and b.ask_depth == 20
    assert b.spread == 3
    assert ob.maker_buy_price(b, fallback_ask=45) == 43   # rest inside the spread
    assert ob.maker_buy_price(ob.parse({}), fallback_ask=50) is None   # no book


def test_orderbook_thin_spread_skips_maker():
    from kalshi.live import orderbook as ob
    b = ob.parse({"orderbook_fp": {"yes_dollars": [["0.44", "10"]], "no_dollars": [["0.55", "10"]]}})
    assert b.spread == 1
    assert ob.maker_buy_price(b, fallback_ask=45, min_spread=3) is None   # too tight to make


def test_maker_price_never_above_intended_ask():
    from kalshi.live import orderbook as ob
    # market moved UP after the planner snapshot (we sized at 44c) -> don't chase
    moved = ob.parse({"orderbook_fp": {"yes_dollars": [["0.60", "20"]], "no_dollars": [["0.35", "20"]]}})
    assert moved.top_bid == 60 and moved.top_ask == 65
    assert ob.maker_buy_price(moved, fallback_ask=44) is None


def test_orderbook_legacy_cents_shape():
    from kalshi.live import orderbook as ob
    b = ob.parse({"orderbook": {"yes": [[42, 10]], "no": [[55, 20]]}})
    assert b.top_bid == 42 and b.top_ask == 45   # 1c level no longer misread as $1


def test_paper_mode_hard_gate():
    from kalshi.engine import should_execute
    assert should_execute("demo", False) is True          # demo executes (sandbox)
    assert should_execute("live", True) is True            # live + enabled -> real
    assert should_execute("live", False) is False          # live, not enabled
    assert should_execute("live", True, paper_mode=True) is False   # HARD gate wins
    assert should_execute("demo", True, paper_mode=True) is False   # even on demo


# --- calibration (data-blocked component, ready to activate) ---

def test_calibration_shrink_and_isotonic():
    from kalshi.calibration import shrink, calibrate
    assert abs(shrink(0.9, strength=0.5) - 0.70) < 1e-9          # pulled toward 0.5
    assert calibrate(0.9, [(0.9, 1)], min_total=100) < 0.9       # thin data -> shrink
    # enough data where 0.8 predictions only win 40% -> calibrated DOWN
    samples = [(0.8, 1)] * 40 + [(0.8, 0)] * 60 + [(0.2, 0)] * 100
    assert calibrate(0.8, samples, min_total=100) < 0.8
