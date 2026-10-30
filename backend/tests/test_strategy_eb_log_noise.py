"""Live log noise on a decision session (alpaca-main, 2026-10-29).

The trend was OFF, the core target 0, and the account already held NO TQQQ.
With remainder books configured, `eb_should_trade` reads a zero core as a
pure-book decision and returns (True, 0.0), so the wrapper treats it as an
"exit". The book was already inside its bands, `targets_to_orders` produced
nothing, and two things went wrong in the log (the orders were right):

1. a red "EXIT PRODUCED NO ORDER" alarm for an exit that had nothing to sell;
2. the cyan plan line was re-written on every one of ~20 ticks, because a
   decision with no orders never consumes the session.

The live log keeps 5000 lines, so that noise pushes real alerts out of it.
These tests pin the fix: logging only, the payload and the cache untouched.
"""
import os
import sys
from datetime import datetime, timedelta, timezone

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

import strategies.strategy_eb as wrapper  # noqa: E402
from strategies.strategy_eb import StrategyEb  # noqa: E402
from strategy_eb import DEFAULTS, LAST_REBALANCE_KEY, LAST_STATE_KEY  # noqa: E402

# Called Thu 2026-06-04, last visible session Wed 2026-06-03 (the decision day).
DECIDES = datetime(2026, 6, 4, 14, 0, tzinfo=timezone.utc)
SESSION = "2026-06-03"
PRICES = {"TQQQ": 80.0, "QQQ": 480.0, "GLD": 370.0, "GDX": 90.0,
          "XLE": 68.0, "BIL": 91.6, "SPY": 500.0}
LEGS = ("TQQQ", "GLD", "GDX", "XLE", "BIL", "SPY")
EXIT_KEY = "_strategy_eb_exit_issued_session"
LOGGED_KEY = "_strategy_eb_logged"
TICKS = 20


def _bars(closes, end_day):
    n = len(closes)
    return [{"t": (end_day - timedelta(days=(n - 1 - i))).isoformat(),
             "c": closes[i]} for i in range(n)]


def declining(n=120, pct=0.01, drift=-0.01, start=300.0):
    """A downtrend with real volatility: the trend machine reads OFF and the
    shipped `core_off_damp = 0` takes the core target to exactly 0."""
    closes = [start]
    for i in range(n - 1):
        closes.append(closes[-1] * (1 + drift)
                      * ((1 + pct) if i % 2 == 0 else (1 - pct)))
    return _bars(closes, datetime(2026, 6, 3, tzinfo=timezone.utc))


def flat(n=120, pct=0.002, start=100.0):
    closes = [start]
    for i in range(n - 1):
        closes.append(closes[-1] * ((1 + pct) if i % 2 == 0 else (1 - pct)))
    return _bars(closes, datetime(2026, 6, 3, tzinfo=timezone.utc))


def data():
    out = {"QQQ": {"bars": declining()}}
    for symbol in LEGS:
        out[symbol] = {"bars": flat()}
    return out


class FakeEmulator:
    def __init__(self, cash=0.0, positions=None):
        self._cash = cash
        self._positions = dict(positions or {})

    def get_cash(self):
        return self._cash

    def get_positions(self):
        return dict(self._positions)

    def get_portfolio_value(self, prices=None):
        px = prices or PRICES
        return self._cash + sum(q * float(px.get(s, 0.0))
                                for s, q in self._positions.items())


def cfg():
    """The shipped bil25 document: books configured, trend filter on."""
    value = dict(DEFAULTS)
    value["strategy_eb_enabled"] = True
    return value


def capture(monkeypatch):
    lines = []
    monkeypatch.setattr(wrapper, "_log",
                        lambda msg, color="white": lines.append((str(msg), color)))
    return lines


def at_target_book():
    """Positions that ARE the trend-OFF target book, NAV $10,000, no TQQQ.

    Derived from the strategy itself rather than hand-copied, so the book is
    inside every band by construction."""
    cache = {}
    StrategyEb().run_once(["TQQQ"], PRICES, DECIDES, cfg(), {}, data=data(),
                          portfolio_emulator=FakeEmulator(cash=10000.0),
                          strategy_cache=cache)
    targets = cache["_strategy_eb_last"]["targets"]
    assert cache["_strategy_eb_last"]["effective_weight"] == 0.0
    assert targets.get("TQQQ", 0.0) == 0.0
    return {s: 10000.0 * w / PRICES[s] for s, w in targets.items() if w > 0}


def run_ticks(positions, cache, ticks=TICKS, cash=0.0):
    strat, outs = StrategyEb(), []
    for i in range(ticks):
        now = DECIDES + timedelta(minutes=15 * i)
        outs.append(strat.run_once(
            ["TQQQ"], PRICES, now, cfg(), {}, data=data(),
            portfolio_emulator=FakeEmulator(cash=cash, positions=positions),
            strategy_cache=cache))
    return outs


# --- 1. the core is already flat ---------------------------------------------

def test_an_exit_with_the_core_already_flat_raises_no_red_alarm(monkeypatch):
    positions = at_target_book()
    assert "TQQQ" not in positions
    lines = capture(monkeypatch)
    run_ticks(positions, {LAST_STATE_KEY: "OFF"})
    red = [m for m, c in lines if c == "red"]
    assert not red, f"a flat core is a no-op, not an alarm: {red}"


def test_the_already_flat_no_op_is_logged_once_at_a_normal_level(monkeypatch):
    positions = at_target_book()
    lines = capture(monkeypatch)
    run_ticks(positions, {LAST_STATE_KEY: "OFF"})
    flat_lines = [(m, c) for m, c in lines if "already flat" in m]
    assert len(flat_lines) == 1, lines
    assert flat_lines[0][1] not in ("red", "yellow")
    assert "TQQQ" in flat_lines[0][0]


def test_a_real_exit_that_produced_no_order_is_still_red(monkeypatch):
    """The alarm stays for the case it was written for: TQQQ is HELD, the
    exit is due, and nothing came out."""
    lines = capture(monkeypatch)
    monkeypatch.setattr(wrapper, "targets_to_orders", lambda *a, **k: ({}, {}))
    run_ticks({"TQQQ": 50.0, "GLD": 10.0}, {}, ticks=3)
    red = [m for m, c in lines if c == "red" and "EXIT PRODUCED NO ORDER" in m]
    assert len(red) == 1, lines


# --- 2. one plan line per decision --------------------------------------------

def test_the_plan_is_logged_once_per_decision_not_once_per_tick(monkeypatch):
    positions = at_target_book()
    lines = capture(monkeypatch)
    run_ticks(positions, {LAST_STATE_KEY: "OFF"})
    plans = [m for m, _ in lines if "| targets=" in m]
    assert len(plans) == 1, f"{len(plans)} plan lines over {TICKS} ticks"
    assert SESSION in plans[0]


def test_a_changed_plan_in_the_same_session_is_logged_again(monkeypatch):
    """The dedupe is on the PLAN, not the session alone: a plan that changes
    mid-session is news and must reach the log."""
    lines = capture(monkeypatch)
    monkeypatch.setattr(wrapper, "targets_to_orders", lambda *a, **k: ({}, {}))
    cache = {}
    run_ticks({"TQQQ": 50.0}, cache, ticks=3)
    run_ticks({"TQQQ": 50.0}, cache, ticks=3, cash=0.0)  # same plan: silent
    assert len([m for m, _ in lines if "| targets=" in m]) == 1
    monkeypatch.setattr(wrapper, "targets_to_orders",
                        lambda *a, **k: ({"TQQQ": -1}, {"TQQQ": 1.0}))
    run_ticks({"TQQQ": 50.0}, cache, ticks=1)
    assert len([m for m, _ in lines if "| targets=" in m]) == 2


def test_the_next_session_logs_its_plan_again(monkeypatch):
    positions = at_target_book()
    lines = capture(monkeypatch)
    cache = {LAST_STATE_KEY: "OFF"}
    run_ticks(positions, cache, ticks=3)
    # A week later, the next Wednesday decision.
    strat = StrategyEb()
    later = DECIDES + timedelta(days=7)
    bars = data()
    for symbol in bars:
        bars[symbol]["bars"] = [dict(b, t=(datetime.fromisoformat(b["t"])
                                           + timedelta(days=7)).isoformat())
                                for b in bars[symbol]["bars"]]
    strat.run_once(["TQQQ"], PRICES, later, cfg(), {}, data=bars,
                   portfolio_emulator=FakeEmulator(positions=positions),
                   strategy_cache=cache)
    assert len([m for m, _ in lines if "| targets=" in m]) == 2


# --- 3. trading behaviour unchanged --------------------------------------------

def test_the_flat_core_no_op_still_sends_nothing_and_records_nothing(monkeypatch):
    positions = at_target_book()
    capture(monkeypatch)
    cache = {LAST_STATE_KEY: "OFF"}
    outs = run_ticks(positions, cache)
    assert outs == [{}] * TICKS
    assert LAST_REBALANCE_KEY not in cache
    assert EXIT_KEY not in cache
    assert set(cache) - {LOGGED_KEY} == {
        LAST_STATE_KEY, "_strategy_eb_trend_state", "_strategy_eb_last"}


def test_a_held_core_still_exits_on_the_first_tick_and_once(monkeypatch):
    capture(monkeypatch)
    cache = {}
    outs = run_ticks({"TQQQ": 50.0}, cache, ticks=4, cash=6000.0)
    assert outs[0].get("TQQQ") == -1
    assert outs[1:] == [{}, {}, {}]
    assert cache[EXIT_KEY] == SESSION
    assert cache[LAST_REBALANCE_KEY] == SESSION
