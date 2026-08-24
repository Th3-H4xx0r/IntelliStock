"""StrategyX.run_once end-to-end against a fake emulator.

Five levers have shipped INERT in this project — each was invisible because its
live path left no fingerprint. These tests exist so that cannot happen here:
they assert the strategy actually emits orders, and that the two default-off
levers are genuinely off rather than accidentally load-bearing.
"""
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from strategies.strategy_x import StrategyX  # noqa: E402


class FakeEmulator:
    def __init__(self, cash=10000.0, positions=None, prices=None):
        self._cash = cash
        self._positions = dict(positions or {})
        self._prices = dict(prices or {})

    def get_cash(self):
        return self._cash

    def get_positions(self):
        return dict(self._positions)

    def get_portfolio_value(self, prices=None):
        px = prices or self._prices
        return self._cash + sum(q * float(px.get(s, 0.0))
                                for s, q in self._positions.items())


def bars(n, start=100.0, step=0.5, end_day=None, mult=None):
    """n daily bars ending on `end_day`, one bar per weekday-ish day."""
    end_day = end_day or datetime(2026, 6, 1, tzinfo=timezone.utc)
    out = []
    for i in range(n):
        ts = end_day - timedelta(days=(n - 1 - i))
        price = start + i * step
        if mult:
            price *= mult(i)
        out.append({"t": ts.isoformat(), "c": price})
    return out


def base_cfg(**over):
    c = {
        "strategy_x_enabled": True,
        "core_bull_symbol": "TQQQ",
        "core_chop_symbol": "SPY",
        "core_bear_symbol": "",
        "core_weight": 0.9,
        "core_band_pct": 0.05,
        "core_filter_symbol": "QQQ",
        "core_filter_ma_bars": 200,
        "core_vol_gate_mult": 1.2,
        "satellite_pct": 0.0,
    }
    c.update(over)
    return c


NOW = datetime(2026, 6, 1, 20, 0, tzinfo=timezone.utc)
PRICES = {"TQQQ": 50.0, "SPY": 500.0, "QQQ": 400.0, "AAPL": 200.0}


def run(cfg, data, emu):
    return StrategyX().run_once(["TQQQ", "SPY"], PRICES, NOW, cfg, {},
                                data=data, portfolio_emulator=emu,
                                strategy_cache={})


def test_disabled_by_default_emits_nothing():
    cfg = base_cfg()
    cfg["strategy_x_enabled"] = False
    assert run(cfg, {"QQQ": {"bars": bars(260)}}, FakeEmulator()) == {}


def test_uptrend_buys_the_levered_core():
    out = run(base_cfg(), {"QQQ": {"bars": bars(260)}},
              FakeEmulator(cash=10000.0, prices=PRICES))
    assert out.get("TQQQ") == 1
    assert out["_nexus_position_sizes"]["TQQQ"]["buy_cash"] > 0
    assert "TQQQ" in out["_nexus_executable_buys"]


def test_downtrend_holds_spy_not_the_levered_core():
    data = {"QQQ": {"bars": bars(260, start=250.0, step=-0.5)}}
    out = run(base_cfg(), data, FakeEmulator(cash=10000.0, prices=PRICES))
    assert out.get("TQQQ") is None
    assert out.get("SPY") == 1


def test_downtrend_sells_a_held_levered_core():
    data = {"QQQ": {"bars": bars(260, start=250.0, step=-0.5)}}
    emu = FakeEmulator(cash=0.0, positions={"TQQQ": 180.0}, prices=PRICES)
    out = run(base_cfg(), data, emu)
    assert out.get("TQQQ") == -1
    assert out["_nexus_position_sizes"]["TQQQ"]["sell_fraction"] == pytest.approx(1.0)
    assert "TQQQ" in out["_nexus_sell_enforcement"]


def test_blind_filter_does_not_buy_the_levered_core():
    """No QQQ bars = no signal. It must hold flat, not default to risk-on."""
    out = run(base_cfg(), {}, FakeEmulator(cash=10000.0, prices=PRICES))
    assert out.get("TQQQ") is None


def test_bear_leg_is_inert_at_its_default():
    data = {"QQQ": {"bars": bars(260, start=250.0, step=-0.5)}}
    out = run(base_cfg(), data, FakeEmulator(cash=10000.0, prices=PRICES))
    assert "SQQQ" not in out


def test_bear_leg_fires_when_configured_and_not_flipping_directly():
    data = {"QQQ": {"bars": bars(260, start=250.0, step=-0.5)}}
    cfg = base_cfg(core_bear_symbol="SQQQ")
    prices = dict(PRICES, SQQQ=20.0)
    emu = FakeEmulator(cash=10000.0, prices=prices)
    out = StrategyX().run_once(["TQQQ"], prices, NOW, cfg, {}, data=data,
                               portfolio_emulator=emu, strategy_cache={})
    assert out.get("SQQQ") == 1


def test_holding_the_bull_leg_blocks_a_direct_flip_to_the_bear_leg():
    data = {"QQQ": {"bars": bars(260, start=250.0, step=-0.5)}}
    cfg = base_cfg(core_bear_symbol="SQQQ")
    prices = dict(PRICES, SQQQ=20.0)
    emu = FakeEmulator(cash=0.0, positions={"TQQQ": 180.0}, prices=prices)
    out = StrategyX().run_once(["TQQQ"], prices, NOW, cfg, {}, data=data,
                               portfolio_emulator=emu, strategy_cache={})
    assert out.get("TQQQ") == -1        # exit the levered long
    assert out.get("SQQQ") is None      # but do NOT enter the levered short


def test_satellite_is_inert_without_conviction_scores():
    data = {"QQQ": {"bars": bars(260)}}
    out = run(base_cfg(satellite_pct=0.2), data,
              FakeEmulator(cash=10000.0, prices=PRICES))
    assert "AAPL" not in out


def test_satellite_buys_ranked_names_when_enabled_and_scored():
    data = {"QQQ": {"bars": bars(260)}, "conviction_scores": {"AAPL": 1.5}}
    out = run(base_cfg(satellite_pct=0.2), data,
              FakeEmulator(cash=10000.0, prices=PRICES))
    assert out.get("AAPL") == 1


def test_commodity_sleeve_buys_a_trending_commodity_end_to_end():
    """Proves the sleeve is not inert through the real run_once path — the
    failure mode this repo hits most is a lever that ships and does nothing."""
    prices = dict(PRICES, GLD=200.0, USO=70.0)
    data = {
        "QQQ": {"bars": bars(260)},
        "GLD": {"bars": bars(200, start=100.0, step=0.6)},    # uptrend
        "USO": {"bars": bars(200, start=200.0, step=-0.4)},   # downtrend
    }
    cfg = base_cfg(commodity_pct=0.15, commodity_max_names=2,
                   commodity_symbols=["GLD", "USO"])
    out = StrategyX().run_once(["TQQQ", "SPY"], prices, NOW, cfg, {}, data=data,
                               portfolio_emulator=FakeEmulator(cash=10000.0,
                                                               prices=prices),
                               strategy_cache={})
    assert out.get("GLD") == 1          # trending -> held
    assert out.get("USO") is None       # falling -> not held
    assert out.get("TQQQ") == 1         # core still funded, just smaller


def test_commodity_sleeve_is_inert_at_its_default():
    out = run(base_cfg(), {"QQQ": {"bars": bars(260)}},
              FakeEmulator(cash=10000.0, prices=PRICES))
    assert "GLD" not in out


def test_cache_records_the_decision_for_the_operator():
    cache = {}
    StrategyX().run_once(["TQQQ"], PRICES, NOW, base_cfg(), {},
                         data={"QQQ": {"bars": bars(260)}},
                         portfolio_emulator=FakeEmulator(cash=10000.0,
                                                         prices=PRICES),
                         strategy_cache=cache)
    last = cache["_strategy_x_last"]
    assert last["risk_on"] is True
    # 259, not 260: these are DAILY bars, whose stamp is the session open while
    # the close is 16:00. The last bar shares its date with the decision, so its
    # close is not knowable yet and pit_daily_closes correctly withholds it.
    assert last["n_closes"] == 259
    assert last["ma"] > 0


def test_future_bars_are_not_visible_to_the_filter():
    """A bar stamped after the decision time must not move the signal.

    The vol gate is disabled here on purpose. With it on, a single injected
    outlier ALSO spikes realised vol, so the gate would force risk-off and the
    test would pass whether or not the cutoff works — passing for the wrong
    reason. Trend alone must carry the assertion.
    """
    cfg = base_cfg(core_vol_gate_mult=0.0)
    good = bars(260, start=250.0, step=-0.5)          # downtrend -> risk-off
    poisoned = good + [{"t": (NOW + timedelta(days=5)).isoformat(),
                        "c": 99999.0}]               # would flip trend risk-on
    emu = FakeEmulator(cash=10000.0, prices=PRICES)
    a = run(cfg, {"QQQ": {"bars": good}}, emu)
    b = run(cfg, {"QQQ": {"bars": poisoned}}, emu)
    assert a.get("TQQQ") is None
    assert a == b
