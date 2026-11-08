"""Wrapper tests for Strategy HT: the broker contract."""
import os
import sys
from datetime import datetime, timedelta, timezone

# ONLY backend/ goes on the path (see test_strategy_xs_run_once.py for why).
_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from strategies.strategy_ht import StrategyHt  # noqa: E402
from strategy_ht import DEFAULTS  # noqa: E402

NOW = datetime(2026, 6, 1, 20, 0, tzinfo=timezone.utc)
PRICES = {"SSO": 100.0, "SPY": 500.0, "BIL": 91.0}


def bars(closes, end_day=None):
    end_day = end_day or datetime(2026, 6, 1, tzinfo=timezone.utc)
    n = len(closes)
    return [{"t": (end_day - timedelta(days=(n - 1 - i))).isoformat(), "c": c}
            for i, c in enumerate(closes)]


def uptrend():      # flat then a decisive break above the +3% band
    return bars([400.0] * 230 + [420.0] * 30)


def downtrend():    # was in, then a decisive break below the -3% band
    return bars([400.0] * 200 + [420.0] * 30 + [380.0] * 30)


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
        return self._cash + sum(q * float(px.get(s, 0.0)) for s, q in self._positions.items())


def cfg(**overrides):
    value = dict(DEFAULTS)
    value["strategy_ht_enabled"] = True
    value.update(overrides)
    return value


def data_for(filter_bars):
    out = {"SPY": {"bars": filter_bars}}
    for s in ("SSO", "BIL"):
        out[s] = {"bars": bars([100.0] * 260)}
    return out


def test_the_class_name_matches_the_brokers_camel_case_resolution():
    assert "".join(w.capitalize() for w in "strategy_ht".split("_")) == StrategyHt.__name__


def test_disabled_by_default_emits_nothing():
    out = StrategyHt().run_once(["SSO"], PRICES, NOW, dict(DEFAULTS), {},
                                data=data_for(uptrend()), portfolio_emulator=FakeEmulator())
    assert out == {}


def test_too_little_history_refuses_to_trade():
    out = StrategyHt().run_once(["SSO"], PRICES, NOW, cfg(), {},
                                data=data_for(bars([400.0] * 120)), portfolio_emulator=FakeEmulator())
    assert out == {}


def test_an_uptrend_buys_the_levered_leg_sized_off_nav():
    cache = {}
    out = StrategyHt().run_once(["SSO"], PRICES, NOW, cfg(), {}, data=data_for(uptrend()),
                                portfolio_emulator=FakeEmulator(cash=10000.0), strategy_cache=cache)
    assert out.get("SSO") == 1
    assert "SPY" not in out and "BIL" not in out
    assert out["_nexus_position_sizes"]["SSO"]["buy_cash"] > 9000.0
    assert cache["_strategy_ht_last"]["in_market"] is True
    assert out["_nexus_discovered"] == ["BIL", "SPY", "SSO"]


def test_a_downtrend_parks_in_the_out_book():
    out = StrategyHt().run_once(["SSO"], PRICES, NOW, cfg(), {}, data=data_for(downtrend()),
                                portfolio_emulator=FakeEmulator(cash=10000.0), strategy_cache={})
    assert out.get("SPY") == 1 and out.get("BIL") == 1
    assert out.get("SSO") is None
    sizes = out["_nexus_position_sizes"]
    assert 4000.0 < sizes["SPY"]["buy_cash"] < 5100.0
    assert 4000.0 < sizes["BIL"]["buy_cash"] < 5100.0


def test_a_flip_to_parked_sells_the_whole_levered_leg_with_the_etf_intent():
    emu = FakeEmulator(cash=0.0, positions={"SSO": 100.0})   # $10k of SSO, no cash
    out = StrategyHt().run_once(["SSO"], PRICES, NOW, cfg(), {}, data=data_for(downtrend()),
                                portfolio_emulator=emu, strategy_cache={})
    assert out.get("SSO") == -1
    assert out["_nexus_position_sizes"]["SSO"]["sell_fraction"] == 1.0
    assert out["_nexus_action_intents"] == {"SSO": "etf_sell"}
    assert out["_nexus_sell_enforcement"] == ["SSO"]


def test_holding_the_target_already_emits_no_churn():
    emu = FakeEmulator(cash=50.0, positions={"SSO": 99.5})
    out = StrategyHt().run_once(["SSO"], PRICES, NOW, cfg(), {}, data=data_for(uptrend()),
                                portfolio_emulator=emu, strategy_cache={})
    assert out == {}


def test_missing_broker_prices_fall_back_to_the_visible_close():
    out = StrategyHt().run_once(["SSO"], {"SPY": 500.0}, NOW, cfg(), {}, data=data_for(uptrend()),
                                portfolio_emulator=FakeEmulator(cash=10000.0, prices={"SPY": 500.0}),
                                strategy_cache={})
    assert out.get("SSO") == 1
