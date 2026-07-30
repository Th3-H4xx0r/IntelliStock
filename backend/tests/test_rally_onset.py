"""Rally-onset flag (2026-07-30, default OFF). The recovery override cannot
fire on the first bars off a V-bottom: its ret5 and off_low predicates both look
BACKWARD ACROSS the crash leg, so on 2026-04-01/04-02 the regime stayed "bear"
while the tape had already reclaimed its 10-day MA ~3% above a 20-session low set
1-2 sessions earlier. max_positions_bear=2 pinned the book at ~85% cash through
the sharpest leg of the rally.

The flag NEVER changes the regime label; it only lets two consumers RELAX a
bear-side restriction (the Z4.1/execution position caps, and the sleeve's bear-leg
ADD). The load-bearing safety property is that it must never fire inside the
2026-03-02..03-30 bear window, whose SQQQ sleeve produces 116% of that window's
profit. See _rally_onset in graph_nexus_analysis.py.
"""
import os
import sys
from datetime import datetime, timedelta

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

import strategies.graph_nexus_analysis as g

# Real SPY daily closes (Nasdaq historical, cross-checked against
# stockanalysis.com to the cent). The 2026-03-30 close of 631.97 is the bottom.
SPY_CLOSES = [
    ("2026-01-29", 694.04), ("2026-01-30", 691.97), ("2026-02-02", 695.41),
    ("2026-02-03", 689.53), ("2026-02-04", 686.19), ("2026-02-05", 677.62),
    ("2026-02-06", 690.62), ("2026-02-09", 693.95), ("2026-02-10", 692.12),
    ("2026-02-11", 691.96), ("2026-02-12", 681.27), ("2026-02-13", 681.75),
    ("2026-02-17", 682.85), ("2026-02-18", 686.29), ("2026-02-19", 684.48),
    ("2026-02-20", 689.43), ("2026-02-23", 682.39), ("2026-02-24", 687.35),
    ("2026-02-25", 693.15), ("2026-02-26", 689.30), ("2026-02-27", 685.99),
    ("2026-03-02", 686.38), ("2026-03-03", 680.33), ("2026-03-04", 685.13),
    ("2026-03-05", 681.31), ("2026-03-06", 672.38), ("2026-03-09", 678.27),
    ("2026-03-10", 677.18), ("2026-03-11", 676.33), ("2026-03-12", 666.06),
    ("2026-03-13", 662.29), ("2026-03-16", 669.03), ("2026-03-17", 670.79),
    ("2026-03-18", 661.43), ("2026-03-19", 659.80), ("2026-03-20", 648.57),
    ("2026-03-23", 655.38), ("2026-03-24", 653.18), ("2026-03-25", 656.82),
    ("2026-03-26", 645.09), ("2026-03-27", 634.09), ("2026-03-30", 631.97),
    ("2026-03-31", 650.34), ("2026-04-01", 655.24), ("2026-04-02", 655.83),
    ("2026-04-06", 658.93), ("2026-04-07", 659.22), ("2026-04-08", 676.01),
    ("2026-04-09", 679.91), ("2026-04-10", 679.46), ("2026-04-13", 686.10),
    ("2026-04-14", 694.46), ("2026-04-15", 699.94), ("2026-04-16", 701.66),
    ("2026-04-17", 710.14), ("2026-04-20", 710.14), ("2026-04-21", 704.08),
    ("2026-04-22", 711.21), ("2026-04-23", 708.45), ("2026-04-24", 713.94),
    ("2026-04-27", 715.17), ("2026-04-28", 711.69), ("2026-04-29", 711.58),
    ("2026-04-30", 718.66)

]

# `regime_recovery_ma_bars` is reused as the short-MA reclaim window. doc-179
# runs 10; the CODE default is 20, which on 2026-04-01 is still above price
# because the pre-crash highs are inside a 20-session mean. The flag therefore
# only bridges the gap at the deployed value — test_code_default_ma_does_not_fire
# below pins that dependency so it cannot regress silently.
ON = {"regime_rally_onset_enabled": True, "regime_recovery_ma_bars": 10}


def _closes_before(date_str):
    """The detector reads closes STRICTLY BEFORE the sim date, so `current` is
    the previous session's close (current = closes[-1])."""
    return [c for d, c in SPY_CLOSES if d < date_str]


def _fires(date_str, cfg=None, diag=None):
    base = dict(ON)
    base.update(cfg or {})
    closes = _closes_before(date_str)
    return g._rally_onset(closes, closes[-1], base, diag if diag is not None else {})


def _bars(start, closes):
    base = datetime.strptime(start, "%Y-%m-%d")
    return [{"t": (base + timedelta(days=i)).strftime("%Y-%m-%dT05:00:00Z"), "c": c}
            for i, c in enumerate(closes)]


def _bear_window_sim_days():
    """Every sim day in the 2026-03-02..03-30 bear window that has >=21 priors."""
    return [d for d, _ in SPY_CLOSES
            if "2026-03-02" <= d <= "2026-03-30" and len(_closes_before(d)) >= 21]


def test_default_off_returns_false_and_stamps_nothing():
    diag = {}
    closes = _closes_before("2026-04-01")
    assert g._rally_onset(closes, closes[-1], {}, diag) is False
    assert "rally_onset" not in diag


def test_fires_on_the_two_bridge_days():
    # 04-01: +2.91% off a low set 1 session ago. 04-02: +3.68%, 2 sessions.
    for day in ("2026-04-01", "2026-04-02"):
        diag = {}
        assert _fires(day, diag=diag) is True, day
        assert diag["rally_onset"]["since_low"] <= 2


def test_regime_label_is_unchanged_when_it_fires():
    """The load-bearing invariant: the flag must not move the label."""
    for day in ("2026-04-01", "2026-04-02"):
        closes = _closes_before(day)
        cfg = {"regime_bear_spy_drawdown_pct": 3.0, "regime_recovery_ma_bars": 10}
        cache_off = {"_overlay_bars_raw": {"SPY": _bars("2026-01-29", closes)}}
        cache_on = {"_overlay_bars_raw": {"SPY": _bars("2026-01-29", closes)}}
        off = g._detect_market_regime(cache_off, cfg, day)
        on = g._detect_market_regime(cache_on, dict(cfg, **ON), day)
        assert on == off, f"{day}: label moved {off} -> {on}"


def test_never_fires_inside_the_bear_window():
    """Guards the +10.17% bear window and the SQQQ sleeve behind it."""
    fired = [d for d in _bear_window_sim_days()
             if _fires(d, cfg={"regime_recovery_ma_bars": 10})]
    assert fired == [], f"rally-onset leaked into the bear window on {fired}"


def test_never_fires_while_making_new_lows():
    # 03-30 and 03-31 sim days: the previous close IS the 20-day low.
    for day in ("2026-03-30", "2026-03-31"):
        assert _fires(day, cfg={"regime_recovery_ma_bars": 10}) is False, day


def test_stale_low_is_rejected():
    # Same bounce shape, but the low sits well outside the freshness window.
    closes = [100.0] * 10 + [80.0] + [95.0] * 10
    assert g._rally_onset(closes, closes[-1], dict(ON), {}) is False


def test_ma_reclaim_is_still_required():
    # Fresh low and a big bounce, but price is still under the short MA.
    closes = [100.0] * 18 + [60.0, 66.0, 67.0]
    cfg = dict(ON, regime_recovery_ma_bars=10, regime_rally_onset_bounce_pct=2.5)
    assert g._rally_onset(closes, closes[-1], cfg, {}) is False


def test_bad_config_values_fall_back_to_defaults_not_true():
    closes = _closes_before("2026-04-01")
    for bad in ("x", None, -1):
        cfg = dict(ON, regime_rally_onset_bounce_pct=bad,
                   regime_rally_onset_max_bars_since_low=bad)
        assert g._rally_onset(closes, closes[-1], cfg, {}) in (True, False)
    # A hostile bounce threshold must SUPPRESS, never invert to always-on.
    cfg = dict(ON, regime_rally_onset_bounce_pct=99.0)
    assert g._rally_onset(closes, closes[-1], cfg, {}) is False


def test_too_few_closes_is_false():
    assert g._rally_onset([100.0] * 5, 100.0, dict(ON), {}) is False


def test_code_default_ma_does_not_fire():
    """Pins the dependency on regime_recovery_ma_bars. At the code default of 20
    the pre-crash highs keep the mean above price on 04-01, so the flag is inert;
    it only bridges the gap at doc-179's deployed value of 10."""
    closes = _closes_before("2026-04-01")
    assert g._rally_onset(closes, closes[-1],
                          {"regime_rally_onset_enabled": True}, {}) is False


def test_bear_window_ceiling_leaves_margin():
    """The one bear bar that clears the MA gate (03-02, via the QQQ warm-up
    proxy) tops out at 1.72% off the 20-day low, against a 2.5% threshold. If a
    future change narrows that, this fails before a backtest wastes hours."""
    worst = 0.0
    for day in _bear_window_sim_days():
        closes = _closes_before(day)
        window = closes[-20:]
        lo = min(window)
        worst = max(worst, (closes[-1] - lo) / lo * 100.0)
    assert worst < 2.5, f"bear window reached {worst:.2f}% off its 20d low"
