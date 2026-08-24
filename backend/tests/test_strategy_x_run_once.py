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


def _crisis_bars(n=400, shock=14):
    """An ORDERLY decline is not a crisis. The gate wants depth AND disorder,
    so the shock has to be violent and recent enough to expand short-window vol
    against the long window."""
    px = [300.0 + i * 0.4 for i in range(n - shock)]
    last = px[-1]
    for i in range(shock):
        last *= (0.94 if i % 2 else 1.01)
        px.append(last)
    end = NOW
    return [{"t": (end - timedelta(days=(n - 1 - i))).isoformat(), "c": p}
            for i, p in enumerate(px)]


def test_bear_leg_does_NOT_fire_on_an_ordinary_downtrend():
    """Risk-off is common; a bad regime is rare. This is the case the symmetric
    flip got wrong, and it is most of the risk-off bars in a real run."""
    data = {"QQQ": {"bars": bars(260, start=250.0, step=-0.5)}}
    prices = dict(PRICES, SQQQ=20.0)
    out = StrategyX().run_once(["TQQQ"], prices, NOW,
                               base_cfg(core_bear_symbol="SQQQ"), {}, data=data,
                               portfolio_emulator=FakeEmulator(cash=10000.0,
                                                               prices=prices),
                               strategy_cache={})
    assert out.get("SQQQ") is None
    assert out.get("SPY") == 1          # risk-off routes to the chop occupant


def test_bear_leg_fires_on_a_detected_crisis():
    data = {"QQQ": {"bars": _crisis_bars()}}
    prices = dict(PRICES, SQQQ=20.0)
    out = StrategyX().run_once(["TQQQ"], prices, NOW,
                               base_cfg(core_bear_symbol="SQQQ"), {}, data=data,
                               portfolio_emulator=FakeEmulator(cash=10000.0,
                                                               prices=prices),
                               strategy_cache={})
    assert out.get("SQQQ") == 1


def test_bear_leg_stands_down_after_the_time_limit():
    data = {"QQQ": {"bars": _crisis_bars()}}
    prices = dict(PRICES, SQQQ=20.0)
    cache = {"_sx_bear_bars": 40}       # already at the limit
    out = StrategyX().run_once(["TQQQ"], prices, NOW,
                               base_cfg(core_bear_symbol="SQQQ",
                                        core_bear_max_bars=40), {}, data=data,
                               portfolio_emulator=FakeEmulator(cash=10000.0,
                                                               prices=prices),
                               strategy_cache=cache)
    assert out.get("SQQQ") is None


def _run_bear(cache, cfg_over=None, bars_override=None):
    prices = dict(PRICES, SQQQ=20.0)
    cfg = base_cfg(core_bear_symbol="SQQQ")
    cfg.update(cfg_over or {})
    data = {"QQQ": {"bars": bars_override or _crisis_bars()}}
    return StrategyX().run_once(["TQQQ"], prices, NOW, cfg, {}, data=data,
                                portfolio_emulator=FakeEmulator(cash=10000.0,
                                                                prices=prices),
                                strategy_cache=cache)


def test_the_time_limit_is_a_real_bound_not_a_cycle():
    """Resetting the counter on the stand-down bar made the limit meaningless:
    it cycled 40-on / 1-off / 40-on forever, and each cycle is a full round trip
    of a -3x leg at the widest spreads of the episode."""
    cache = {"_sx_bear_bars": 40}
    out = _run_bear(cache, {"core_bear_max_bars": 40,
                            "core_bear_cooldown_bars": 20})
    assert out.get("SQQQ") is None
    assert cache.get("_sx_bear_cooldown", 0) > 0, "no cooldown — it re-engages"
    out2 = _run_bear(cache, {"core_bear_max_bars": 40,
                             "core_bear_cooldown_bars": 20})
    assert out2.get("SQQQ") is None
    assert cache["_sx_bear_bars"] == 0


def test_the_counter_only_ticks_while_the_leg_is_actually_held():
    """The gate can be open while the trend filter is still risk-on, in which
    case no SQQQ exists and the -6*sigma^2 clock must not be running."""
    cache = {}
    _run_bear(cache, bars_override=bars(400))          # uptrend -> risk-on
    assert cache.get("_sx_bear_bars", 0) == 0


def test_the_leg_engages_and_starts_its_clock():
    cache = {}
    assert _run_bear(cache).get("SQQQ") == 1
    assert cache["_sx_bear_bars"] == 1
    assert cache["_sx_bear_grace"] > 0                 # exit hysteresis armed


def test_the_strategy_declares_its_own_universe():
    """It must not depend on the instance watchlist listing TQQQ/SQQQ/QQQ."""
    data = {"QQQ": {"bars": bars(260)}}
    out = StrategyX().run_once([], PRICES, NOW,
                               base_cfg(core_bear_symbol="SQQQ"), {}, data=data,
                               portfolio_emulator=FakeEmulator(cash=10000.0,
                                                               prices=PRICES),
                               strategy_cache={})
    assert set(out.get("_nexus_discovered") or []) >= {"QQQ", "TQQQ", "SPY",
                                                       "SQQQ"}


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
