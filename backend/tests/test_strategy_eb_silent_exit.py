"""A2 + A1: the wrapper must not record a decision it never executed.

Two silent failures on the LIVE path of a 3x fund:

A1  `targets_to_orders` skips any leg whose price is <= 0 (strategy_x.py:1178,
    1203). Live, `prices` is keyed off the operator's WATCHLIST and the EB legs
    are not on it, so a tick where the fallback close is also missing hands the
    core a price of 0 — the exit's sell leg is dropped with no log, and worse,
    `held` is computed as 0 so `eb_should_trade` reads a full core position as
    FLAT and can size a fresh buy on top of it.

A2  The four cache keys were written on the DECISION, not on the execution. An
    exit that produced no orders still stamped `_strategy_eb_exit_issued_session`
    and was then suppressed for the remaining ~26 ticks of the session, and a
    buy set the pending-order guard blocked in full still consumed
    `_eb_last_rebalance_session`.
"""
import os
import sys
from datetime import datetime, timedelta, timezone

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

import strategies.strategy_eb as wrapper  # noqa: E402
from strategies.strategy_eb import StrategyEb  # noqa: E402
from strategy_eb import DEFAULTS, LAST_REBALANCE_KEY  # noqa: E402

DECIDES = datetime(2026, 6, 4, 20, 0, tzinfo=timezone.utc)
DECISION_SESSION = "2026-06-03"
PRICES = {"TQQQ": 80.0, "SPY": 500.0, "BIL": 91.0, "QQQ": 480.0}

EXIT_KEY = "_strategy_eb_exit_issued_session"
PENDING_KEY = "_strategy_eb_pending_targets"
STATE_KEY = "_strategy_eb_last_state"


def alternating(pct, n=120, end_day=None, start=100.0):
    end_day = end_day or datetime(2026, 6, 3, tzinfo=timezone.utc)
    closes = [start]
    for i in range(n - 1):
        closes.append(closes[-1] * ((1 + pct) if i % 2 == 0 else (1 - pct)))
    return [{"t": (end_day - timedelta(days=(n - 1 - i))).isoformat(),
             "c": closes[i]} for i in range(n)]


class FakeEmulator:
    def __init__(self, cash=10000.0, positions=None, prices=None):
        self._cash = cash
        self._positions = dict(positions or {})
        self._prices = dict(prices or PRICES)

    def get_cash(self):
        return self._cash

    def get_positions(self):
        return dict(self._positions)

    def get_portfolio_value(self, prices=None):
        px = prices or self._prices
        return self._cash + sum(q * float(px.get(s, 0.0))
                                for s, q in self._positions.items())


def cfg(**overrides):
    value = dict(DEFAULTS)
    value["strategy_eb_enabled"] = True
    value.update(overrides)
    return value


def data_for(ref_bars, legs=("TQQQ", "SPY", "BIL")):
    out = {"QQQ": {"bars": ref_bars}}
    for symbol in legs:
        out[symbol] = {"bars": alternating(0.002)}
    return out


def capture(monkeypatch):
    lines = []
    monkeypatch.setattr(wrapper, "_log",
                        lambda msg, color="white": lines.append((str(msg), color)))
    return lines


def untouched(cache):
    for key in (LAST_REBALANCE_KEY, EXIT_KEY, PENDING_KEY, STATE_KEY):
        assert key not in cache, f"{key} was written by a tick that traded nothing"


# --- A1: a core the strategy cannot price -----------------------------------

def test_a_core_with_no_usable_price_refuses_the_tick_and_says_so(monkeypatch):
    """Holding the core, blind to its price: the exit's sell leg would be
    silently dropped and the buy leg sized against a phantom-flat book."""
    lines = capture(monkeypatch)
    cache = {}
    blind = {s: p for s, p in PRICES.items() if s != "TQQQ"}
    data = data_for(alternating(0.10))
    data["TQQQ"] = {"bars": []}
    out = StrategyEb().run_once(
        ["TQQQ"], blind, DECIDES, cfg(), {}, data=data,
        portfolio_emulator=FakeEmulator(cash=5000.0, positions={"TQQQ": 100.0},
                                        prices=PRICES),
        strategy_cache=cache)
    assert out == {}
    untouched(cache)
    red = [m for m, c in lines if c == "red"]
    assert red, f"the silent branch: no red log, only {lines}"
    assert "TQQQ" in red[0]


def test_a_priced_core_still_exits(monkeypatch):
    """The guard must not swallow the ordinary exit it sits in front of."""
    capture(monkeypatch)
    cache = {}
    out = StrategyEb().run_once(
        ["TQQQ"], PRICES, DECIDES, cfg(), {}, data=data_for(alternating(0.10)),
        portfolio_emulator=FakeEmulator(cash=0.0, positions={"TQQQ": 100.0}),
        strategy_cache=cache)
    assert out.get("TQQQ") == -1
    assert cache[EXIT_KEY] == DECISION_SESSION


def test_a_core_the_book_does_not_hold_needs_no_price(monkeypatch):
    """A flat book has nothing to mis-size, so a missing core price is just a
    leg that cannot be bought — the pre-existing behaviour, unchanged."""
    capture(monkeypatch)
    blind = {s: p for s, p in PRICES.items() if s != "TQQQ"}
    data = data_for(alternating(0.002))
    data["TQQQ"] = {"bars": []}
    out = StrategyEb().run_once(["TQQQ"], blind, DECIDES, cfg(), {}, data=data,
                                portfolio_emulator=FakeEmulator(cash=10000.0),
                                strategy_cache={})
    assert "TQQQ" not in out


# --- A2: a decision that produced no orders ---------------------------------

def test_an_exit_that_produced_no_order_is_not_marked_issued(monkeypatch):
    """The exact A2 shape: `targets_to_orders` returns nothing, so the exit was
    never sent — but the old code stamped the session and suppressed the retry
    on every one of the session's remaining ticks."""
    capture(monkeypatch)
    monkeypatch.setattr(wrapper, "targets_to_orders", lambda *a, **k: ({}, {}))
    cache = {}
    out = StrategyEb().run_once(
        ["TQQQ"], PRICES, DECIDES, cfg(), {}, data=data_for(alternating(0.10)),
        portfolio_emulator=FakeEmulator(cash=0.0, positions={"TQQQ": 100.0}),
        strategy_cache=cache)
    assert out == {}
    untouched(cache)


def test_a_fully_blocked_buy_set_does_not_consume_the_session(monkeypatch):
    """The pending-order guard runs BEFORE the cache writes. A tick whose every
    buy it blocked has decided nothing, so the session must stay open."""
    capture(monkeypatch)

    class Unreadable(FakeEmulator):
        def pending_execution_symbols(self):
            raise RuntimeError("orders endpoint unreachable")

    cache = {}
    out = StrategyEb().run_once(
        ["TQQQ"], PRICES, DECIDES, cfg(pending_buy_guard_enabled=True), {},
        data=data_for(alternating(0.002)),
        portfolio_emulator=Unreadable(cash=10000.0), strategy_cache=cache)
    assert out == {}
    untouched(cache)


def test_a_tick_that_did_trade_still_records_the_session(monkeypatch):
    capture(monkeypatch)
    cache = {}
    out = StrategyEb().run_once(
        ["TQQQ"], PRICES, DECIDES, cfg(), {}, data=data_for(alternating(0.002)),
        portfolio_emulator=FakeEmulator(cash=10000.0), strategy_cache=cache)
    assert out.get("TQQQ") == 1
    assert cache[LAST_REBALANCE_KEY] == DECISION_SESSION
    assert cache[PENDING_KEY]


# --- A3: the three remaining unlogged refusals ------------------------------

def test_a_missing_emulator_is_reported(monkeypatch):
    """Live, no emulator means the broker handed the lane nothing to read the
    book from. Returning {} for that is right; returning it in silence means a
    lane can be inert for a whole deployment with no line to find."""
    lines = capture(monkeypatch)
    assert StrategyEb().run_once(["TQQQ"], PRICES, DECIDES, cfg(), {},
                                 data=data_for(alternating(0.01)),
                                 portfolio_emulator=None) == {}
    assert [m for m, c in lines if c == "red"], f"silent, only {lines}"


def test_a_non_positive_nav_is_reported(monkeypatch):
    """NAV <= 0 is an unreadable account, not an empty one — most often every
    held leg priced at 0, which is the same blindness A1 guards."""
    lines = capture(monkeypatch)
    assert StrategyEb().run_once(["TQQQ"], PRICES, DECIDES, cfg(), {},
                                 data=data_for(alternating(0.01)),
                                 portfolio_emulator=FakeEmulator(cash=0.0),
                                 strategy_cache={}) == {}
    red = [m for m, c in lines if c == "red"]
    assert red, f"silent, only {lines}"
    assert "nav" in red[0].lower()


def test_a_non_positive_book_nav_is_reported(monkeypatch):
    """Sharing the document with a sibling lane, the sibling can hold the whole
    account. This book then has nothing to size against — which is a
    CONFIGURATION outcome an operator needs to see, not a silent no-op."""
    lines = capture(monkeypatch)
    prices = dict(PRICES, AAPL=100.0)
    out = StrategyEb().run_once(
        ["TQQQ"], prices, DECIDES, cfg(reserve_for_other_lanes_pct=0.9), {},
        data=data_for(alternating(0.01)),
        portfolio_emulator=FakeEmulator(cash=0.0, positions={"AAPL": 100.0},
                                        prices=prices),
        strategy_cache={})
    assert out == {}
    red = [m for m, c in lines if c == "red"]
    assert red, f"silent, only {lines}"
    assert "book" in red[0].lower()


def test_the_refusals_are_throttled_to_one_line_a_session(monkeypatch):
    """~26 ticks a session at 15m. A refusal that repeats on all of them is how
    a real refusal goes unread."""
    lines = capture(monkeypatch)
    cache = {}
    for _ in range(4):
        StrategyEb().run_once(["TQQQ"], PRICES, DECIDES, cfg(), {},
                              data=data_for(alternating(0.01)),
                              portfolio_emulator=FakeEmulator(cash=0.0),
                              strategy_cache=cache)
    assert len([m for m, c in lines if c == "red"]) == 1, lines


def test_the_exit_re_arms_next_tick_when_its_order_never_came_out(monkeypatch):
    """End to end: a dropped exit is retried on the very next tick of the same
    session, because nothing recorded it as issued."""
    capture(monkeypatch)
    strat, cache = StrategyEb(), {}
    emu = FakeEmulator(cash=0.0, positions={"TQQQ": 100.0})
    monkeypatch.setattr(wrapper, "targets_to_orders", lambda *a, **k: ({}, {}))
    assert strat.run_once(["TQQQ"], PRICES, DECIDES, cfg(), {},
                          data=data_for(alternating(0.10)),
                          portfolio_emulator=emu, strategy_cache=cache) == {}
    monkeypatch.undo()
    capture(monkeypatch)
    retry = strat.run_once(["TQQQ"], PRICES, DECIDES, cfg(), {},
                           data=data_for(alternating(0.10)),
                           portfolio_emulator=emu, strategy_cache=cache)
    assert retry.get("TQQQ") == -1, "the dropped exit was never retried"
