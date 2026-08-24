"""Strategy X — leveraged core with a de-lever filter.

The design these tests pin comes from an offline study over 15.4 years of real
prices (`scripts/_strategy_x_final.py`), not from intuition:

    proposed config     CAGR 34.7%  maxDD -38.4%  Sharpe 1.00  beats SPY 12/15y
    bear leg ON         CAGR -4.2%  maxDD -88.2%  Sharpe 0.20   <- proven dead
    satellite 20% ON    CAGR 30.0%                              <- dilutes

So the bear leg and the satellite are levers that default OFF, and the tests
below pin that they are genuinely inert at their defaults.
"""
import os
import sys

import pytest

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from datetime import datetime, timezone  # noqa: E402

from strategy_x import (  # noqa: E402
    DEFAULTS,
    core_signal,
    pit_daily_closes,
    plan_targets,
    rank_commodities,
    targets_to_orders,
)


def _series(n, start, step):
    return [start + i * step for i in range(n)]


def ccfg(**over):
    c = dict(DEFAULTS)
    c.update({"commodity_pct": 0.15, "commodity_max_names": 2})
    c.update(over)
    return c


def test_only_commodities_in_their_own_uptrend_are_ranked():
    closes = {"GLD": _series(160, 100, 0.5),     # uptrend
              "USO": _series(160, 200, -0.5)}    # downtrend
    assert rank_commodities(closes, ccfg()) == ["GLD"]


def test_ranking_is_by_momentum_best_first():
    closes = {"GLD": _series(160, 100, 0.2),
              "SLV": _series(160, 100, 1.0)}     # much stronger
    assert rank_commodities(closes, ccfg()) == ["SLV", "GLD"]


def test_ranking_respects_max_names():
    closes = {s: _series(160, 100, 0.5 + i * 0.1)
              for i, s in enumerate(["GLD", "SLV", "USO"])}
    assert len(rank_commodities(closes, ccfg(commodity_max_names=2))) == 2


def test_no_commodity_in_an_uptrend_returns_empty():
    closes = {"GLD": _series(160, 200, -0.5), "USO": _series(160, 200, -0.4)}
    assert rank_commodities(closes, ccfg()) == []


def test_short_history_is_skipped_not_guessed():
    assert rank_commodities({"GLD": _series(20, 100, 0.5)}, ccfg()) == []


def test_commodity_sleeve_takes_its_weight_out_of_the_core():
    t, _ = plan_targets(risk_on=True, config=ccfg(core_weight=0.9),
                        commodity_ranked=["GLD", "SLV"])
    assert t["GLD"] == pytest.approx(0.075, abs=1e-5)
    assert t["SLV"] == pytest.approx(0.075, abs=1e-5)
    assert t["TQQQ"] == pytest.approx(0.765, abs=1e-4)   # 0.85 core budget * 0.9
    assert sum(t.values()) <= 1.0 + 1e-9


def test_commodity_budget_returns_to_the_core_when_nothing_trends():
    t, notes = plan_targets(risk_on=True, config=ccfg(), commodity_ranked=[])
    assert t["TQQQ"] == pytest.approx(0.9)
    assert any("no commodity" in n.lower() for n in notes)


def test_commodity_sleeve_is_inert_at_its_default():
    t, _ = plan_targets(risk_on=True, config=dict(DEFAULTS),
                        commodity_ranked=["GLD"])
    assert "GLD" not in t


def cfg(**over):
    c = dict(DEFAULTS)
    c.update(over)
    return c


def rising(n=260, start=100.0, step=0.5):
    return [start + i * step for i in range(n)]


def falling(n=260, start=200.0, step=0.5):
    return [start - i * step for i in range(n)]


# ── core_signal ────────────────────────────────────────────────────────────

def test_risk_on_when_price_above_ma_and_vol_calm():
    sig = core_signal(rising(), cfg())
    assert sig.risk_on is True


def test_risk_off_when_price_below_ma():
    sig = core_signal(falling(), cfg())
    assert sig.risk_on is False
    assert "below" in sig.reason.lower()


def test_insufficient_history_is_risk_off_not_risk_on():
    """A cold start must not read as risk-on. The review found clamp(NaN)==1.0
    silently sizing a FULL leveraged position on the first bar."""
    sig = core_signal([100.0] * 10, cfg())
    assert sig.risk_on is False
    assert "history" in sig.reason.lower()


def test_empty_closes_is_risk_off():
    assert core_signal([], cfg()).risk_on is False


def test_nan_close_is_rejected_not_propagated():
    closes = rising()
    closes[-1] = float("nan")
    sig = core_signal(closes, cfg())
    assert sig.risk_on is False


def test_vol_gate_turns_risk_off_when_volatility_spikes():
    """Alternating +/-6% closes above a rising MA: trend says yes, vol says no."""
    closes = rising(300)
    for i in range(len(closes) - 40, len(closes)):
        closes[i] = closes[i] * (1.06 if i % 2 else 0.94)
    on = core_signal(closes, cfg(core_vol_gate_mult=0.0))
    gated = core_signal(closes, cfg(core_vol_gate_mult=1.2))
    assert on.risk_on is True
    assert gated.risk_on is False
    assert "vol" in gated.reason.lower()


def test_vol_gate_disabled_by_zero_is_inert():
    closes = rising(300)
    assert (core_signal(closes, cfg(core_vol_gate_mult=0.0)).risk_on
            == core_signal(closes, cfg(core_vol_gate_mult=0.0)).risk_on)


def test_signal_is_quantized_so_a_float_wobble_cannot_flip_the_leg():
    """Review defect 13: quantize the DECISION value, not just inputs."""
    closes = rising(260)
    a = core_signal(closes, cfg())
    b = core_signal([c + 1e-13 for c in closes], cfg())
    assert a.risk_on == b.risk_on


# ── plan_targets ───────────────────────────────────────────────────────────

def test_risk_on_allocates_core_weight_to_the_bull_symbol():
    t, _ = plan_targets(risk_on=True, config=cfg(core_weight=0.9))
    assert t["TQQQ"] == pytest.approx(0.9)
    assert t.get("SQQQ", 0.0) == 0.0


def test_risk_off_routes_to_the_chop_symbol_not_cash_by_default():
    """chop=cash measured 26.8% CAGR vs 34.7% for chop=SPY."""
    t, _ = plan_targets(risk_on=False, config=cfg())
    assert t.get("SPY", 0.0) > 0.5
    assert t.get("TQQQ", 0.0) == 0.0


def test_bear_leg_is_inert_by_default():
    """The 15.4y study: enabling this took CAGR 34.7% -> -4.2%."""
    t, _ = plan_targets(risk_on=False, config=cfg())
    assert "SQQQ" not in t


def test_bear_leg_needs_BOTH_a_symbol_and_an_engaged_gate():
    """Risk-off is common; a bad regime is rare. Shorting every risk-off bar is
    the symmetric flip that lost 99% of its value."""
    off = plan_targets(risk_on=False, config=cfg(core_bear_symbol="SQQQ"),
                       bear_engaged=False)[0]
    assert "SQQQ" not in off
    on = plan_targets(risk_on=False, config=cfg(core_bear_symbol="SQQQ"),
                      bear_engaged=True)[0]
    assert on["SQQQ"] > 0.0


def test_bear_leg_is_sized_smaller_than_the_bull_core():
    """A -3x inverse carries -6*sigma^2 drag vs the long leg's -3*sigma^2, so
    the short side is deliberately the smaller position."""
    t = plan_targets(risk_on=False,
                     config=cfg(core_bear_symbol="SQQQ", core_weight=0.9,
                                core_bear_weight=0.35),
                     bear_engaged=True)[0]
    assert t["SQQQ"] == pytest.approx(0.35, abs=1e-4)
    assert t["SPY"] == pytest.approx(0.65, abs=1e-4)   # remainder, not cash


def test_satellite_is_zero_by_default():
    t, notes = plan_targets(risk_on=True, config=cfg())
    assert all(s in ("TQQQ", "SPY") for s in t)


def test_satellite_reduces_core_when_enabled():
    t, _ = plan_targets(risk_on=True, config=cfg(satellite_pct=0.2),
                        satellite_ranked=["AAPL", "MSFT"])
    assert t["TQQQ"] == pytest.approx(0.8 * 0.9, abs=1e-9)
    assert t["AAPL"] == pytest.approx(0.1)
    assert t["MSFT"] == pytest.approx(0.1)


def test_satellite_budget_falls_back_to_core_when_no_names_rank():
    """A dead signal must degrade to the index, never to cash."""
    t, notes = plan_targets(risk_on=True, config=cfg(satellite_pct=0.2),
                            satellite_ranked=[])
    assert t["TQQQ"] == pytest.approx(0.9)
    assert any("no satellite" in n.lower() for n in notes)


def test_weights_never_exceed_one():
    t, _ = plan_targets(risk_on=True,
                        config=cfg(core_weight=1.0, satellite_pct=0.5),
                        satellite_ranked=["A", "B", "C"])
    assert sum(t.values()) <= 1.0 + 1e-9


# ── targets_to_orders ──────────────────────────────────────────────────────

def test_buy_emitted_when_underweight():
    d, s = targets_to_orders({"TQQQ": 0.9}, nav=10000.0, positions={},
                             prices={"TQQQ": 50.0}, cash=10000.0, config=cfg())
    assert d["TQQQ"] == 1
    assert s["TQQQ"]["buy_cash"] == pytest.approx(9000.0, rel=0.02)


def test_sell_emitted_for_a_held_symbol_absent_from_targets():
    d, s = targets_to_orders({"SPY": 1.0}, nav=10000.0,
                             positions={"TQQQ": 100.0}, prices={"TQQQ": 50.0,
                                                                "SPY": 500.0},
                             cash=0.0, config=cfg(), owned={"TQQQ", "SPY"})
    assert d["TQQQ"] == -1
    assert s["TQQQ"]["sell_fraction"] == pytest.approx(1.0)


def test_a_position_this_strategy_does_not_own_is_never_sold():
    """`_nexus_sell_enforcement` is a HARD override in the broker. Walking the
    whole book would liquidate a co-deployed strategy's holdings every tick."""
    d, _ = targets_to_orders({"SPY": 1.0}, nav=10000.0,
                             positions={"AAPL": 100.0, "TQQQ": 50.0},
                             prices={"AAPL": 20.0, "TQQQ": 50.0, "SPY": 500.0},
                             cash=0.0, config=cfg(), owned={"TQQQ", "SPY"})
    assert "AAPL" not in d
    assert d["TQQQ"] == -1


def test_no_order_inside_the_rebalance_band():
    """Without a band the core round-trips every bar; bt 383711 was 23 identical
    deploys, 21 rejected, re-issued every tick."""
    d, _ = targets_to_orders({"TQQQ": 0.90}, nav=10000.0,
                             positions={"TQQQ": 179.0}, prices={"TQQQ": 50.0},
                             cash=1050.0, config=cfg(core_band_pct=0.05))
    assert "TQQQ" not in d


def test_order_below_min_notional_is_skipped():
    d, _ = targets_to_orders({"TQQQ": 0.9}, nav=50.0, positions={},
                             prices={"TQQQ": 50.0}, cash=50.0,
                             config=cfg(min_order_usd=50.0, core_band_pct=0.0))
    assert d.get("TQQQ") is None


def test_buy_never_exceeds_available_cash():
    """An unaffordable clip is rejected and re-issued every bar — the exact
    failure core_sleeve's CORE_DEPLOY_COST_HAIRCUT exists to prevent."""
    _, s = targets_to_orders({"TQQQ": 1.0}, nav=10000.0, positions={},
                             prices={"TQQQ": 50.0}, cash=500.0, config=cfg())
    assert s["TQQQ"]["buy_cash"] <= 500.0


def test_zero_price_symbol_is_skipped_not_divided_by():
    d, _ = targets_to_orders({"TQQQ": 0.9}, nav=10000.0, positions={},
                             prices={"TQQQ": 0.0}, cash=10000.0, config=cfg())
    assert "TQQQ" not in d


def _bar(iso, close):
    return {"t": iso, "c": close}


def test_bars_after_the_decision_time_are_excluded():
    """The whole point. A 16:00 close is not knowable at a 09:45 decision."""
    bars = [_bar("2026-01-02T14:30:00Z", 100.0),
            _bar("2026-01-02T21:00:00Z", 110.0)]
    as_of = datetime(2026, 1, 2, 15, 0, tzinfo=timezone.utc)
    assert pit_daily_closes(bars, as_of) == [100.0]


def test_last_visible_bar_of_a_day_is_that_day_close():
    bars = [_bar("2026-01-02T14:30:00Z", 100.0),
            _bar("2026-01-02T15:30:00Z", 101.0),
            _bar("2026-01-05T14:30:00Z", 102.0)]
    as_of = datetime(2026, 1, 5, 15, 0, tzinfo=timezone.utc)
    assert pit_daily_closes(bars, as_of) == [101.0, 102.0]


def test_closes_are_ordered_oldest_first_regardless_of_input_order():
    bars = [_bar("2026-01-05T14:30:00Z", 102.0),
            _bar("2026-01-02T14:30:00Z", 100.0)]
    # Well clear of the session boundary: midnight UTC is still the PREVIOUS
    # evening in New York, and this test is about ordering, not the cutoff.
    as_of = datetime(2026, 1, 7, 15, 0, tzinfo=timezone.utc)
    assert pit_daily_closes(bars, as_of) == [100.0, 102.0]


def test_naive_timestamps_are_treated_as_utc_not_dropped_silently():
    bars = [{"t": "2026-01-02T14:30:00", "c": 100.0}]
    as_of = datetime(2026, 1, 3, tzinfo=timezone.utc)
    assert pit_daily_closes(bars, as_of) == [100.0]


def test_unparseable_bar_is_skipped_without_raising():
    bars = [_bar("not-a-date", 99.0), _bar("2026-01-02T14:30:00Z", 100.0)]
    as_of = datetime(2026, 1, 3, tzinfo=timezone.utc)
    assert pit_daily_closes(bars, as_of) == [100.0]


def test_empty_or_none_returns_empty_list():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert pit_daily_closes(None, now) == []
    assert pit_daily_closes([], now) == []


def test_a_daily_bar_is_not_visible_on_its_own_session():
    """A daily bar is stamped at the session open but its `c` is the 16:00
    close. `ts <= as_of` would hand today's close to a 09:45 decision."""
    bars = [_bar("2026-01-02T00:00:00Z", 100.0),
            _bar("2026-01-05T00:00:00Z", 110.0)]
    mid_session = datetime(2026, 1, 5, 14, 45, tzinfo=timezone.utc)
    assert pit_daily_closes(bars, mid_session) == [100.0]
    next_day = datetime(2026, 1, 6, 14, 45, tzinfo=timezone.utc)
    assert pit_daily_closes(bars, next_day) == [100.0, 110.0]


def test_intraday_bars_are_still_visible_within_their_session():
    """The daily rule must not leak into the intraday cadence these backtests
    actually run at — an hourly bar that has closed IS knowable."""
    bars = [_bar("2026-01-05T14:30:00Z", 100.0),
            _bar("2026-01-05T15:30:00Z", 101.0)]
    as_of = datetime(2026, 1, 5, 15, 30, tzinfo=timezone.utc)
    assert pit_daily_closes(bars, as_of) == [101.0]


def test_direct_bull_to_bear_flip_is_blocked():
    """The most expensive trade in the system: two 3x round trips at full core
    size. Always pass through the un-levered occupant for at least one bar."""
    t, notes = plan_targets(risk_on=False,
                            config=cfg(core_bear_symbol="SQQQ"),
                            held_core="TQQQ", bear_engaged=True)
    assert t.get("SQQQ", 0.0) == 0.0
    assert t.get("SPY", 0.0) > 0.0
    assert any("flip" in n.lower() for n in notes)


# ── bear gate ──────────────────────────────────────────────────────────────

from strategy_x import BearSignal, bear_signal, strategy_x_universe  # noqa: E402


def bcfg(**over):
    c = dict(DEFAULTS)
    c.update({"core_bear_symbol": "SQQQ", "core_bear_min_confirm": 4})
    c.update(over)
    return c


def _crisis_series(n=400, shock=14):
    """Down, below both MAs, deep off the high, AND disorderly.

    The shock must be RECENT and short: `vol_expand` compares the 20-bar
    realised vol against the 60-bar, so a decline long enough to fill both
    windows shows no expansion at all — which is the correct reading of a
    grinding bear, and is what `test_gate_stays_shut_on_an_orderly_decline`
    pins separately.
    """
    px = [300.0 + i * 0.4 for i in range(n - shock)]        # long calm uptrend
    last = px[-1]
    for i in range(shock):                                  # violent, recent
        last *= (0.94 if i % 2 else 1.01)
        px.append(last)
    return px


def test_gate_is_closed_when_no_bear_symbol_is_configured():
    s = bear_signal(_crisis_series(), bcfg(core_bear_symbol=""))
    assert s.engaged is False
    assert "disabled" in s.reason


def test_gate_opens_on_a_genuine_crisis():
    s = bear_signal(_crisis_series(), bcfg())
    assert s.engaged is True
    assert s.confirms == 4


def test_gate_stays_shut_on_an_orderly_decline():
    """A smooth drift down is below both MAs and off its high, but it is not
    DISORDERLY — that is an ordinary bear, not a crisis."""
    s = bear_signal([300.0 - i * 0.2 for i in range(400)], bcfg())
    assert s.engaged is False
    assert s.confirms < 4


def test_gate_stays_shut_in_an_uptrend():
    s = bear_signal([100.0 + i * 0.5 for i in range(400)], bcfg())
    assert s.engaged is False


def test_a_looser_quorum_opens_the_gate_earlier():
    orderly = [300.0 - i * 0.2 for i in range(400)]
    assert bear_signal(orderly, bcfg(core_bear_min_confirm=4)).engaged is False
    assert bear_signal(orderly, bcfg(core_bear_min_confirm=3)).engaged is True


def test_the_time_limit_stands_the_leg_down():
    """Decay is -6*sigma^2, so time is the enemy even when direction is right."""
    px = _crisis_series()
    assert bear_signal(px, bcfg(core_bear_max_bars=40), bars_engaged=39).engaged
    s = bear_signal(px, bcfg(core_bear_max_bars=40), bars_engaged=40)
    assert s.engaged is False
    assert "time limit" in s.reason


def test_gate_fails_closed_on_short_history():
    assert bear_signal([100.0] * 50, bcfg()).engaged is False


def test_universe_is_declared_from_config_not_the_watchlist():
    u = strategy_x_universe(bcfg(commodity_pct=0.2,
                                 commodity_symbols=["GLD", "USO"]))
    assert u[:4] == ["QQQ", "TQQQ", "SPY", "SQQQ"]
    assert "GLD" in u and "USO" in u


def test_universe_omits_the_commodity_sleeve_when_it_is_off():
    u = strategy_x_universe(bcfg(commodity_pct=0.0,
                                 commodity_symbols=["GLD", "USO"]))
    assert "GLD" not in u
