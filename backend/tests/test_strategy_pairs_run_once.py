"""Wrapper tests for Strategy Pairs: the broker contract."""
import os
import sys
from datetime import datetime, timedelta, timezone

# ONLY backend/ goes on the path (see test_strategy_xs_run_once.py for why).
_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from strategies.strategy_pairs import StrategyPairs  # noqa: E402
from strategy_pairs import DEFAULTS  # noqa: E402

NOW = datetime(2026, 6, 2, 20, 0, tzinfo=timezone.utc)
PRICES = {"KO": 50.0, "PEP": 100.0}


def bars(closes, end_day=None):
    """Daily bars whose LAST close is the session before NOW."""
    end_day = end_day or datetime(2026, 6, 1, tzinfo=timezone.utc)
    n = len(closes)
    return [{"t": (end_day - timedelta(days=(n - 1 - i))).isoformat(), "c": c}
            for i, c in enumerate(closes)]


class FakeEmulator:
    def __init__(self, cash=10000.0, positions=None, pending_credit=0.0):
        self._cash = cash
        self._positions = dict(positions or {})
        self._pending_credit = pending_credit

    def get_cash(self):
        return self._cash

    def get_buying_power(self, reserved=0.0, *, prices=None):
        return self._cash + self._pending_credit

    def get_positions(self):
        return dict(self._positions)

    def get_portfolio_value(self, prices=None):
        px = prices or PRICES
        return self._cash + self._pending_credit + sum(
            q * float(px.get(s, 0.0)) for s, q in self._positions.items())


def cfg(**overrides):
    value = dict(DEFAULTS)
    value.update({"strategy_pairs_enabled": True, "pairs": [["KO", "PEP"]]})
    value.update(overrides)
    return value


def data(ko, pep):
    return {"KO": {"bars": bars(ko)}, "PEP": {"bars": bars(pep)}}


FLAT = [50.0] * 10
KO_LAGS = data(FLAT, [100.0] * 9 + [104.0])       # PEP ran ahead
PEP_LAGS = data([50.0] * 9 + [52.0], [100.0] * 10)


def run(config, market, emulator, cache=None, now=NOW, prices=PRICES):
    return StrategyPairs().run_once(
        ["KO", "PEP"], prices, now, config, {}, data=market,
        portfolio_emulator=emulator,
        strategy_cache=cache if cache is not None else {})


def test_the_class_name_matches_the_brokers_camel_case_resolution():
    assert "".join(w.capitalize() for w in "strategy_pairs".split("_")) \
        == StrategyPairs.__name__


def test_disabled_or_blind_does_nothing():
    assert run(cfg(strategy_pairs_enabled=False), KO_LAGS, FakeEmulator()) == {}
    assert run(cfg(), None, FakeEmulator()) == {}          # live: data=None
    assert StrategyPairs().run_once([], PRICES, NOW, cfg(), {}, data=KO_LAGS) == {}


def test_hold_opens_the_fifty_fifty_book():
    out = run(cfg(mode="hold"), KO_LAGS, FakeEmulator())
    assert out["KO"] == 1 and out["PEP"] == 1
    sizes = out["_nexus_position_sizes"]
    assert abs(sizes["KO"]["buy_cash"] - sizes["PEP"]["buy_cash"]) < 100
    assert sizes["_cash_reserve_floor_pct"] == 0.0


def test_reversal_buys_only_the_laggard():
    out = run(cfg(mode="reversal"), KO_LAGS, FakeEmulator())
    assert out["KO"] == 1 and "PEP" not in out
    assert out["_nexus_position_sizes"]["KO"]["buy_cash"] > 9000


def test_momentum_buys_the_leader():
    out = run(cfg(mode="momentum"), KO_LAGS, FakeEmulator())
    assert out["PEP"] == 1 and "KO" not in out


def test_a_flip_sells_the_old_leg_and_buys_the_new_one_on_the_same_tick():
    # Holding KO; now PEP is the laggard. The sell's proceeds are credited.
    emulator = FakeEmulator(cash=0.0, positions={"KO": 200.0},
                            pending_credit=0.0)
    out = run(cfg(mode="reversal"), PEP_LAGS, emulator)
    assert out["KO"] == -1
    assert out["_nexus_position_sizes"]["KO"]["sell_fraction"] == 1.0
    assert out["_nexus_action_intents"] == {"KO": "etf_sell"}
    assert "KO" in out["_nexus_sell_enforcement"]
    # The buy goes out in the same payload, sized against 95% of the sale.
    assert out["PEP"] == 1
    buy = out["_nexus_position_sizes"]["PEP"]["buy_cash"]
    assert 9300 < buy <= 9500


def test_without_the_credit_flag_the_flip_only_sells():
    emulator = FakeEmulator(cash=0.0, positions={"KO": 200.0})
    out = run(cfg(mode="reversal", backtest_credit_pending_sell_proceeds=False),
              PEP_LAGS, emulator)
    assert out["KO"] == -1 and "PEP" not in out


def test_the_tick_after_an_order_is_sat_out():
    cache = {}
    assert run(cfg(mode="reversal"), KO_LAGS, FakeEmulator(), cache)["KO"] == 1
    # The order is not in the book yet: the next tick does nothing ...
    assert run(cfg(mode="reversal"), KO_LAGS, FakeEmulator(), cache) == {}
    # ... and the one after decides again.
    assert run(cfg(mode="reversal"), KO_LAGS, FakeEmulator(), cache)["KO"] == 1


def test_a_tick_with_no_orders_does_not_arm_the_sit_out():
    cache = {}
    held = FakeEmulator(cash=0.0, positions={"KO": 200.0})
    assert run(cfg(mode="reversal"), KO_LAGS, held, cache) == {}
    assert "_strategy_pairs_sit_out" not in cache


def test_no_orders_when_the_book_is_already_on_target():
    emulator = FakeEmulator(cash=0.0, positions={"KO": 200.0})
    assert run(cfg(mode="reversal"), KO_LAGS, emulator) == {}


def test_todays_bar_is_not_visible():
    # The laggard only shows up in a bar stamped on the decision's own date.
    today = datetime(2026, 6, 2, tzinfo=timezone.utc)
    market = {"KO": {"bars": bars(FLAT, end_day=today)},
              "PEP": {"bars": bars([100.0] * 9 + [104.0], end_day=today)}}
    out = run(cfg(mode="reversal"), market, FakeEmulator())
    # Without today's close the two legs tie, so the book opens 50/50.
    assert out["KO"] == 1 and out["PEP"] == 1


def test_the_zscore_state_steps_once_per_session():
    wide = [100.0 + (i % 2) * 0.5 for i in range(59)] + [112.0]   # PEP rich
    market = data([50.0] * 60, wide)
    cache = {}
    c = cfg(mode="zscore")
    out = run(c, market, FakeEmulator(), cache)
    assert out["KO"] == 1 and "PEP" not in out                    # hold cheap KO
    assert cache["_strategy_pairs_signals"] == {"KO/PEP": 1}
    # A second tick in the same session replays the cached signal.
    cache["_strategy_pairs_signals"] = {"KO/PEP": -1}
    cache.pop("_strategy_pairs_sit_out", None)
    out = run(c, market, FakeEmulator(), cache)
    assert out["PEP"] == 1 and "KO" not in out


def test_an_unpriced_leg_stays_in_cash():
    market = {"KO": {"bars": bars(FLAT)}, "PEP": {"bars": []}}
    out = run(cfg(mode="hold"), market, FakeEmulator(), prices={"KO": 50.0})
    assert out["KO"] == 1 and "PEP" not in out
    assert out["_nexus_position_sizes"]["KO"]["buy_cash"] < 5100
