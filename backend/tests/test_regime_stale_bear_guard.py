"""Phase 3 (2026-07-20 bull-alpha): stale-bear-trigger recovery guard.

bt 148462 forensics: the bull window opened classified BEAR on a day-1 ret20 of
-8.18% that was inherited from the PRIOR month's drawdown (a QQQ proxy, already
mostly recovered). That stale trigger parked 35% in SQQQ (-$236) and locked the
book to cap=2 for a week of a +13.6% SPY month. Raising the threshold was
refuted (it delays the REAL bear month's protective cap by ~2.5 weeks). The fix
targets staleness: suppress the ret20-based bear when the drawdown that produced
it has already recovered >= X of its peak-to-trough range off the low. Default 0
(off) — must be a no-op unless configured; the ma_200 structural bear is never
suppressed; a still-falling market (fresh bear) is never suppressed.
"""
import os
import sys
from datetime import datetime, timedelta

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

import strategies.graph_nexus_analysis as g


def _bars(start: str, closes):
    base = datetime.strptime(start, "%Y-%m-%d")
    return [
        {"t": (base + timedelta(days=i)).strftime("%Y-%m-%dT05:00:00Z"), "c": c}
        for i, c in enumerate(closes)
    ]


def _regime(closes, cfg=None, start="2025-10-01", date_key="2026-06-01"):
    # 3.0 is the deployed bear threshold (doc-179 patch); the code default is
    # 5.0. Pin it so the -4%..-8% recovered/falling cases classify as bear.
    base = {"regime_bear_spy_drawdown_pct": 3.0}
    base.update(cfg or {})
    cache = {"_overlay_bars_raw": {"SPY": _bars(start, closes)}}
    return g._detect_market_regime(cache, base, date_key), cache


# A 25-bar window that ret20 reads as bear (-8%) and is STILL FALLING:
# current == the window low. Guard must never suppress this.
_FALLING = [100.0] * 5 + [100.0, 98.0, 96.0, 94.0, 92.0, 91.0, 90.5, 90.2,
                          90.1, 90.05, 90.02, 90.01, 90.0, 89.5, 89.0, 88.5,
                          88.0, 87.5, 87.0, 86.0]

# A 25-bar window: peak 100 -> deep dip 82 -> recovered to 96 (current). ret20
# still negative (< -3) but the drawdown has bounced ~78% off its low.
_RECOVERED = [100.0, 100.0, 100.0, 100.0, 100.0,  # baseline (older than the 20d window)
              100.0, 97.0, 93.0, 88.0, 84.0, 82.0, 83.0, 85.0, 87.0, 89.0,
              90.0, 91.0, 92.0, 93.0, 94.0, 95.0, 95.5, 95.8, 95.9, 96.0]


def test_falling_market_still_bear_guard_on():
    r, _ = _regime(_FALLING, {"regime_bear_stale_recovery_pct": 0.5})
    assert r == "bear", "a still-falling market must stay bear even with the guard on"


def test_recovered_drawdown_suppressed_when_guard_on():
    r, cache = _regime(_RECOVERED, {"regime_bear_stale_recovery_pct": 0.5})
    assert r != "bear", "a mostly-recovered drawdown must not read as a fresh bear"
    assert (cache.get("_market_regime_diag") or {}).get("raw") != "bear"


def test_recovered_drawdown_is_bear_when_guard_off():
    r, _ = _regime(_RECOVERED, {})  # default 0 = off
    assert r == "bear", "default (guard off) must reproduce today's bear classification"


def test_recovered_drawdown_is_bear_when_guard_zero_explicit():
    r, _ = _regime(_RECOVERED, {"regime_bear_stale_recovery_pct": 0.0})
    assert r == "bear"


def test_ma200_structural_bear_never_suppressed():
    # 200+ closes below their 200-day MA, with a recovered-off-low tail so the
    # ret20 branch WOULD be suppressed — the ma_200 branch must still fire bear.
    closes = [100.0] * 195 + [100.0, 96.0, 92.0, 88.0, 84.0, 80.0, 83.0, 86.0,
                              89.0, 92.0]  # 205 closes; current 92 < ma200 (~99.5)
    r, cache = _regime(closes, {"regime_bear_stale_recovery_pct": 0.5},
                       start="2025-01-01")
    assert r == "bear", "structural (< 200d MA) bear is not a stale drawdown"
