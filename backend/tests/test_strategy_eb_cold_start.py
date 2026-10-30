"""A cold start must never buy before it has real targets.

2026-09-16, alpaca-main (real money): the instance started with an empty
`strategy_cache`, so there was no pending book for the cash sweep to deploy
toward. The sweep's fallback planned the remainder around the core it held
(zero) in the trend state it had never measured (the "ON" default), and put
100% of NAV into the trend-ON book: GLD 7.577 @ 398.25, GDX 15.908 @ 94.95,
XLE 23.222 @ 64.72 — 50/25/25 of $6,041.43. QQQ's 2026-09-15 close was 1.6%
under its 25-session average, so the rule's real answer was trend OFF: no core,
the OFF book (37.5/18.75/18.75) and 25% BIL. The next decision day then had to
rotate the whole book.

The fixtures are the producers' own: the instance's doc-200 config as stored,
the backtest emulator the broker builds, and QQQ's actual daily closes.
"""
import os
import sys
from datetime import datetime, timedelta, timezone

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from portfolio_emulator import create_backtest_emulator  # noqa: E402
from strategies.strategy_eb import StrategyEb  # noqa: E402
from strategy_eb import (  # noqa: E402
    eb_core_weight, eb_targets, eb_trend_state,
)

#: alpaca-main's Strategy EB config (doc 200 "Strategy EB bil25") as the
#: instance stored it on 2026-10-30.
LIVE_CFG = {
    "broker_max_single_position_pct": 0.95, "cash_sweep_min_pct": 0.02,
    "cash_symbol": "BIL", "core_band_pct": 0.03, "core_leverage": 3,
    "core_max_weight": 0.65, "core_off_damp": 0, "core_rebalance_band": 0.1,
    "core_symbol": "TQQQ", "cost_haircut_pct": 0.005,
    "honour_single_position_cap": True, "live_hard_drawdown": 0.35,
    "live_kill_drawdown": 0.45, "live_max_leveraged_fraction": 0.7,
    "live_max_order_fraction": 0.7, "live_max_symbol_fraction": 0.7,
    "live_soft_drawdown": 0.25, "min_history_bars": 70, "min_order_usd": 25,
    "off_symbol": "SPY", "pending_buy_guard_enabled": False,
    "rebalance_weekdays": [2], "reference_symbol": "QQQ",
    "remainder_bil_fraction": 0, "reserve_for_other_lanes_pct": 0,
    "risk_off_symbol": "BIL", "strategy_eb_enabled": True, "target_vol": 0.2,
    "trend_filter_bars": 25,
    "trend_off_book": {"GDX": 0.1875, "GLD": 0.375, "XLE": 0.1875},
    "trend_off_enter_pct": 0.01,
    "trend_on_book": {"GDX": 0.25, "GLD": 0.5, "XLE": 0.25},
    "trend_on_exit_pct": 0.02, "vol_fast_bars": 10, "vol_slow_bars": 40,
    "vts_enabled": False, "vts_median_bars": 250, "vts_mid_symbol": "VIXM",
    "vts_short_symbol": "VIXY", "vts_threshold": 1, "weight_step": 0.05,
}

#: QQQ daily closes (adjusted), the 80 sessions 2026-05-21 .. 2026-09-15.
QQQ_TO_0915 = [
    712.98, 716.01, 728.72, 727.89, 734.03, 736.73, 741.15, 744.57,
    742.62, 739.03, 703.55, 714.54, 706.32, 692.21, 715.59, 719.80,
    742.41, 728.30, 720.97, 739.04, 737.18, 712.91, 709.88, 715.63,
    705.78, 723.33, 735.63, 724.42, 711.86, 722.07, 708.69, 710.70,
    722.53, 724.75, 711.00, 718.94, 716.99, 705.21, 694.61, 695.34,
    708.23, 704.62, 691.24, 683.52, 681.41, 674.79, 661.04, 682.84,
    687.27, 699.34, 723.10, 716.55, 713.91, 722.28, 720.12, 717.70,
    722.95, 731.31, 730.31, 729.11, 716.76, 715.33, 710.19, 712.70,
    705.58, 709.98, 710.63, 720.36, 715.68, 716.01, 706.90, 708.50,
    716.92, 718.21, 717.61, 715.56, 707.95, 714.14, 708.44, 703.81,
]

#: Wednesday 2026-09-16, 09:35 ET: the last visible session is Tuesday 09-15,
#: which is NOT a decision weekday — the tick that swept on 09-16.
OPEN_0916 = datetime(2026, 9, 16, 13, 35, tzinfo=timezone.utc)

#: The legs' marks that morning (the 09-16 fills; TQQQ/SPY/BIL 09-15 closes).
PRICES_0916 = {"QQQ": 703.81, "TQQQ": 80.39, "SPY": 755.51, "BIL": 91.25,
               "GLD": 398.25, "GDX": 94.95, "XLE": 64.72}

BASELINE = 6041.43
#: `targets_to_orders` funds the last leg it sizes from what the 0.5% cost
#: haircut leaves, so a leg can land a few dollars under its share. The ON and
#: OFF books differ by 12.5% of NAV on GLD alone; this cannot blur them.
TOL = 0.005 * BASELINE


def _weekday_bars(closes, last_day):
    """Daily bars stamped on consecutive weekdays, the last on `last_day`."""
    days, day = [], last_day
    while len(days) < len(closes):
        if day.weekday() < 5:
            days.append(day)
        day -= timedelta(days=1)
    days.reverse()
    return [{"t": d.isoformat(), "c": c} for d, c in zip(days, closes)]


def _data(ref_closes, last_day):
    out = {"QQQ": {"bars": _weekday_bars(ref_closes, last_day)}}
    for symbol, px in PRICES_0916.items():
        if symbol != "QQQ":
            out[symbol] = {"bars": _weekday_bars([px] * 80, last_day)}
    return out


def _fresh_account():
    return create_backtest_emulator(initial_cash=BASELINE, taker_fee=None,
                                    is_crypto=False,
                                    execution_delay=timedelta(days=1))


def _buy_cash(out):
    sizes = out.get("_nexus_position_sizes") or {}
    return {s: sizes[s]["buy_cash"] for s, d in out.items()
            if not s.startswith("_") and d == 1}


def test_the_0916_cold_start_buys_the_trend_off_book_not_the_trend_on_book():
    cache = {}
    out = StrategyEb().run_once(
        ["TQQQ"], dict(PRICES_0916), OPEN_0916, dict(LIVE_CFG), {},
        data=_data(QQQ_TO_0915, datetime(2026, 9, 15, tzinfo=timezone.utc)),
        portfolio_emulator=_fresh_account(), strategy_cache=cache)

    buys = _buy_cash(out)
    # Trend OFF with `core_off_damp: 0` holds no core, and the OFF state's
    # quarter of the remainder is BIL.
    assert "TQQQ" not in out
    assert out.get("BIL") == 1, f"no BIL leg: {buys}"
    assert abs(buys["BIL"] - 0.25 * BASELINE) < TOL
    # GLD at the OFF book's 37.5%, not the ON book's 50% the 09-16 sweep bought.
    assert abs(buys["GLD"] - 0.375 * BASELINE) < TOL, buys
    assert abs(buys["GDX"] - 0.1875 * BASELINE) < TOL, buys
    assert abs(buys["XLE"] - 0.1875 * BASELINE) < TOL, buys
    assert sum(buys.values()) <= BASELINE + 1e-6
    assert all(d == 1 for s, d in out.items() if not s.startswith("_"))


def test_a_cold_start_plans_exactly_what_a_decision_session_would():
    """The same pure path a decision session runs, prev state unknown."""
    cfg = dict(LIVE_CFG)
    state = eb_trend_state(QQQ_TO_0915, None, cfg)
    assert state == "OFF"
    want = eb_targets(eb_core_weight(QQQ_TO_0915, cfg, state), cfg, state)
    assert want == {"BIL": 0.25, "GLD": 0.375, "GDX": 0.1875, "XLE": 0.1875}

    out = StrategyEb().run_once(
        ["TQQQ"], dict(PRICES_0916), OPEN_0916, cfg, {},
        data=_data(QQQ_TO_0915, datetime(2026, 9, 15, tzinfo=timezone.utc)),
        portfolio_emulator=_fresh_account(), strategy_cache={})
    assert set(_buy_cash(out)) == set(want)


def _calm_tape(n=80):
    """Alternating +/-1% around a flat level: the last close sits inside the
    25-session dead zone (state ON from a cold start) and realised vol is
    ~17%, so the vol-targeted core is 0.20 / (3 * 0.17) -> 0.35."""
    closes = [700.0]
    for i in range(n - 1):
        closes.append(closes[-1] * (1.01 if i % 2 == 0 else 0.99))
    return closes


def test_a_cold_start_on_an_uptrend_buys_the_vol_targeted_core_too():
    """Trend ON: the target set is the core plus the ON book around it — never
    the ON book stretched over 100% of NAV."""
    closes = _calm_tape()
    cfg = dict(LIVE_CFG)
    assert eb_trend_state(closes, None, cfg) == "ON"
    core = eb_core_weight(closes, cfg, "ON")
    assert core == 0.35
    want = eb_targets(core, cfg, "ON")
    assert want == {"TQQQ": 0.35, "GLD": 0.325, "GDX": 0.1625, "XLE": 0.1625}

    out = StrategyEb().run_once(
        ["TQQQ"], dict(PRICES_0916), OPEN_0916, cfg, {},
        data=_data(closes, datetime(2026, 9, 15, tzinfo=timezone.utc)),
        portfolio_emulator=_fresh_account(), strategy_cache={})

    buys = _buy_cash(out)
    assert out.get("TQQQ") == 1, f"core missing from a cold start: {buys}"
    for leg in ("GLD", "GDX", "XLE"):
        assert abs(buys[leg] - want[leg] * BASELINE) < TOL, buys
    assert "BIL" not in out and "SPY" not in out


def test_a_cold_start_never_writes_the_trend_state_off_cadence():
    """The machine is evaluated, and persisted, on decision sessions only; a
    cold-start sweep measures it for its own plan and leaves the cache alone."""
    cache = {}
    StrategyEb().run_once(
        ["TQQQ"], dict(PRICES_0916), OPEN_0916, dict(LIVE_CFG), {},
        data=_data(QQQ_TO_0915, datetime(2026, 9, 15, tzinfo=timezone.utc)),
        portfolio_emulator=_fresh_account(), strategy_cache=cache)
    assert "_strategy_eb_trend_state" not in cache
    assert "_strategy_eb_pending_targets" not in cache


def test_a_sweep_with_no_book_and_no_closes_buys_nothing():
    """Fail closed: with neither a decided book nor the history to decide one,
    there are no real targets, so nothing is bought."""
    emu = _fresh_account()
    out = StrategyEb()._sweep(
        dict(LIVE_CFG), {}, "2026-09-15",
        ["QQQ", "TQQQ", "SPY", "BIL", "GLD", "GDX", "XLE"],
        dict(PRICES_0916), emu.get_portfolio_value(PRICES_0916),
        emu.get_positions(), emu, 0.0, "ON", closes=None)
    assert out == {}
