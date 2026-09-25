"""The put order: ST's collateral tests (tests/test_collateral_cap.py) ported,
the delta pick and price ladder of place_put_order, and fixes 1, 3, 8, 9."""
import os
import sys
from types import SimpleNamespace as NS

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from swing_trader import wheel_rules as wr  # noqa: E402

EXP = "2026-06-12"


def contract(strike, symbol=None, close=2.0, expiry=EXP, kind="put", underlying="Q"):
    return NS(symbol=symbol or f"{underlying}260612P{int(strike * 1000):08d}",
              underlying=underlying, option_type=kind, strike=strike,
              expiration=expiry, open_interest=100, close_price=close)


def snap(delta, bid):
    return NS(bid=bid, ask=None, last=None, iv=None, delta=delta, gamma=None,
              theta=None, vega=None, quote_ts=None)


def candidate(symbol, strike, contracts=1, est_premium=2.0):
    return {"symbol": symbol, "strike_price": strike, "expiry": EXP,
            "position_size_contracts": contracts, "est_premium": est_premium}


def order_for(strike, contracts=1, *, equity=100_000, cash=150_000, bid=1.80,
              existing=0.0, reserved=0.0):
    c = contract(strike)
    return wr.build_put_order(candidate("Q", strike, contracts), chain=[c],
                              snapshots={c.symbol: snap(-0.25, bid)}, equity=equity,
                              cash=cash, existing_underlying_collateral=existing,
                              reserved_collateral=reserved, signal_id="sig-1",
                              session="2026-06-01")


# -- ST tests/test_collateral_cap.py -----------------------------------------

def test_put_blocked_when_strike_exceeds_collateral_cap():
    order, error, _ = order_for(790.0, bid=30.0)
    assert order is None and "Collateral cap" in error


def test_contracts_reduced_to_fit_collateral_cap():
    order, error, meta = order_for(100.0, contracts=3, bid=1.50)
    assert error is None and order["qty"] == 2
    assert any("reducing 3 → 2" in n for n in meta["notes"])


def test_put_within_cap_placed_unchanged():
    order, error, _ = order_for(130.0, bid=1.80)
    assert error is None and order["qty"] == 1


# -- fixes -------------------------------------------------------------------

def test_fix_3_the_cap_counts_puts_already_on_the_underlying():
    order, error, _ = order_for(130.0, existing=13_000.0)
    assert order is None and "Collateral cap" in error
    order, error, _ = order_for(100.0, contracts=2, existing=5_000.0)
    assert order["qty"] == 2       # 25k cap - 5k = 20k fits two $10k puts


def test_fix_8_collateral_is_checked_against_cash_not_margin():
    order, error, _ = order_for(130.0, cash=20_000.0, reserved=13_000.0)
    assert order is None and error.startswith("Insufficient cash")
    order, _, meta = order_for(100.0, contracts=2, cash=30_000.0, reserved=15_000.0)
    assert order["qty"] == 1 and any("reducing to 1" in n for n in meta["notes"])


def test_fix_1_duplicates_are_refused():
    short_put = NS(symbol="Q260605P00100000", underlying="Q", option_type="put",
                   strike=100.0, expiry="2026-06-05", qty=-1, avg_entry_price=1.0,
                   market_value=-100.0)
    short_call = NS(symbol="Q260605C00150000", underlying="Q", option_type="call",
                    strike=150.0, expiry="2026-06-05", qty=-1, avg_entry_price=1.0,
                    market_value=-100.0)
    working = NS(symbol="Q260612P00095000", side="sell", qty=1, filled_qty=0,
                 position_intent="sell_to_open")
    assert "open short put" in wr.duplicate_put_reason("Q", [short_put], [])
    assert wr.duplicate_put_reason("Q", [short_call], []) is None
    assert "working sell order" in wr.duplicate_put_reason("Q", [], [working])
    assert "already ordered" in wr.duplicate_put_reason("q", [], [], {"Q"})
    assert wr.duplicate_put_reason("R", [short_put], [working]) is None


def test_collateral_totals_read_contract_fields_and_working_orders():
    positions = [NS(symbol="A1", underlying="A", option_type="put", strike=50.0, qty=-2),
                 NS(symbol="A2", underlying="A", option_type="call", strike=60.0, qty=-1),
                 NS(symbol="B1", underlying="B", option_type="put", strike=20.0, qty=1)]
    assert wr.short_put_collateral(positions) == (10_000.0, {"A": 10_000.0})
    orders = [NS(symbol="C260612P00040000", side="sell", qty=3, filled_qty=1,
                 position_intent="sell_to_open"),
              NS(symbol="C260612P00040000", side="buy", qty=1, filled_qty=0,
                 position_intent="buy_to_close"),
              NS(symbol="AAPL", side="sell", qty=10, filled_qty=0, position_intent=None)]
    assert wr.pending_sto_collateral(orders) == (8_000.0, {"C": 8_000.0})


# -- place_put_order's contract choice and price ------------------------------

def test_the_contract_closest_to_the_target_delta_wins():
    chain = [contract(95.0), contract(100.0), contract(105.0)]
    snaps = {chain[0].symbol: snap(-0.18, 0.9), chain[1].symbol: snap(-0.24, 1.4),
             chain[2].symbol: snap(-0.33, 2.2)}
    order, _, meta = wr.build_put_order(candidate("Q", 100.0), chain=chain, snapshots=snaps,
                                        equity=100_000, cash=100_000)
    assert order["contract"] == chain[1].symbol and order["strike"] == 100.0
    assert order["limit_price"] == round(1.4 * 0.95, 2) and meta["price_source"] == "bid"


def test_no_greeks_falls_back_to_the_highest_strike_at_or_below_target():
    chain = [contract(95.0, close=1.5), contract(99.0, close=1.2), contract(101.0)]
    order, _, meta = wr.build_put_order(candidate("Q", 100.0), chain=chain,
                                        snapshots={}, equity=100_000, cash=100_000)
    assert order["strike"] == 99.0
    assert order["limit_price"] == round(1.2 * 0.90, 2) and meta["price_source"] == "close"


def test_the_price_ladder():
    assert wr.limit_price_ladder(delta_bid=1.00, contract_close=2.0, est_premium=0.5) == (0.95, "bid")
    assert wr.limit_price_ladder(delta_bid=0.04, contract_close=2.0, est_premium=0.5) == (1.8, "close")
    assert wr.limit_price_ladder(delta_bid=0.0, contract_close=None, est_premium=0.5) == (0.5, "est_premium")


def test_a_sub_penny_limit_is_refused():
    c = contract(100.0, close=None)
    order, error, _ = wr.build_put_order(candidate("Q", 100.0, est_premium=0.004), chain=[c],
                                         snapshots={}, equity=100_000, cash=100_000)
    assert order is None and error.startswith("Limit price too low")


def test_the_order_is_the_contract_option_order_shape():
    order, _, _ = order_for(130.0)
    assert order == {
        "signal_id": "sig-1", "session": "2026-06-01", "underlying": "Q",
        "contract": order["contract"], "option_type": "put", "strike": 130.0,
        "expiry": EXP, "position_intent": "sell_to_open", "qty": 1,
        "order_type": "limit", "limit_price": round(1.80 * 0.95, 2), "tif": "day",
        "reason": "wheel_sto_put"}


def test_no_chain_for_the_expiry_is_a_skip_not_a_crash():
    other = contract(100.0, expiry="2026-06-19")
    order, error, _ = wr.build_put_order(candidate("Q", 100.0), chain=[other],
                                         snapshots={}, equity=100_000, cash=100_000)
    assert order is None and "No put contracts" in error


def test_fix_9_the_live_build_reads_the_whole_chain_through_the_adapter():
    chain = [contract(90.0 + i) for i in range(30)]

    class Adapter:
        def __init__(self):
            self.calls = []

        def get_option_contracts(self, underlying, **kw):
            self.calls.append((underlying, kw))
            return chain

        def get_option_snapshots(self, symbols):
            self.snap_request = list(symbols)
            return {s: snap(-0.25 if s == chain[20].symbol else -0.05, 1.0) for s in symbols}

    a = Adapter()
    order, error, _ = wr.build_put_order_live(
        candidate("Q", 110.0), adapter=a, cfg={}, equity=100_000, cash=100_000,
        option_positions=[], open_orders=[], signal_id="s", session="2026-06-01")
    assert a.calls == [("Q", {"option_type": "put", "expiration_gte": EXP,
                              "expiration_lte": EXP})]
    assert error is None and order["contract"] == chain[20].symbol
    assert all(abs(float(s[-8:]) / 1000 - 110.0) / 110.0 <= 0.15 for s in a.snap_request)


# -- hardening beyond the brief -----------------------------------------------

def test_a_snapshot_outside_the_nearby_strikes_never_pairs_with_a_stale_strike():
    # ST only asked for nearby snapshots, so every key was nearby. A pure call
    # handed more must not return one contract with another contract's strike.
    near, far = contract(100.0), contract(150.0)
    snaps = {near.symbol: snap(-0.18, 1.0), far.symbol: snap(-0.25, 3.0)}
    strike, sym, bid, _diff = wr.pick_put_by_delta([near], snaps)
    assert (strike, sym, bid) == (100.0, near.symbol, 1.0)


class _ChainAdapter:
    def __init__(self, chain):
        self.chain = chain

    def get_option_contracts(self, underlying, **kw):
        return self.chain

    def get_option_snapshots(self, symbols):
        return {s: snap(-0.25, 1.0) for s in symbols}


def test_a_committed_put_already_on_the_book_is_not_counted_twice():
    # A resumed scan: the put ordered on R earlier in this scan is now a
    # working order (then a position). Cash 30k - 15k leaves room for one Q put.
    c = contract(100.0)
    working = NS(symbol="R260612P00150000", side="sell", qty=1, filled_qty=0,
                 position_intent="sell_to_open")
    held = NS(symbol="R260612P00150000", underlying="R", option_type="put",
              strike=150.0, expiry=EXP, qty=-1, avg_entry_price=1.0, market_value=-100.0)
    for positions, orders in (([], [working]), ([held], [])):
        order, error, _ = wr.build_put_order_live(
            candidate("Q", 100.0), adapter=_ChainAdapter([c]), cfg={}, equity=100_000,
            cash=30_000, option_positions=positions, open_orders=orders,
            committed_collateral={"R": 15_000.0}, signal_id="s", session="2026-06-01")
        assert error is None and order["qty"] == 1


def test_a_committed_put_not_yet_on_the_book_still_reserves_cash():
    c = contract(100.0)
    order, error, _ = wr.build_put_order_live(
        candidate("Q", 100.0), adapter=_ChainAdapter([c]), cfg={}, equity=100_000,
        cash=20_000, option_positions=[], open_orders=[],
        committed_collateral={"R": 15_000.0}, signal_id="s", session="2026-06-01")
    assert order is None and error.startswith("Insufficient cash")
