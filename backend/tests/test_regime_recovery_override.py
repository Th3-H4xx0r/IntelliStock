"""Regime recovery-override (2026-07-21): catch a bull turn early instead of
sitting defensive on a stale 20-day return. When the 20d return still reads
bear but THREE signals agree — (1) ret5 thrust up, (2) price back above the
20d MA, (3) recovered >= Y off the 20d low — reclassify bear -> chop (or bull
on a strong thrust). A weak dead-cat bounce fails >=1 gate and stays bear.
Default OFF. See _detect_market_regime in graph_nexus_analysis.py.
"""
import os
import sys
from datetime import datetime, timedelta

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

import strategies.graph_nexus_analysis as g


def _bars(start, closes):
    base = datetime.strptime(start, "%Y-%m-%d")
    return [{"t": (base + timedelta(days=i)).strftime("%Y-%m-%dT05:00:00Z"), "c": c}
            for i, c in enumerate(closes)]


def _regime(closes, cfg=None, start="2025-10-01", date_key="2026-06-01"):
    base = {"regime_bear_spy_drawdown_pct": 3.0}  # deployed value
    base.update(cfg or {})
    cache = {"_overlay_bars_raw": {"SPY": _bars(start, closes)}}
    return g._detect_market_regime(cache, base, date_key), cache


ON = {"regime_recovery_override_enabled": True}

# Deepening bear: monotonic decline, current == the low, price below 20d MA.
_DEEPENING = [100.0 - i for i in range(21)]  # 100..80

# Recovering: drop to 82 then a real rally back to 92.5; ret5 in [2,5) -> chop.
_RECOVER_CHOP = [100.0, 100.0, 96.0, 92.0, 88.0, 85.0, 83.0, 82.0, 83.0, 85.0,
                 86.0, 87.0, 88.0, 89.0, 89.5, 90.0, 90.5, 91.0, 91.5, 92.0, 92.5]

# Strong thrust: last 5 days rip -> ret5 >= 5 -> bull.
_RECOVER_BULL = [100.0, 100.0, 96.0, 92.0, 88.0, 85.0, 83.0, 82.0, 82.5, 83.0,
                 83.5, 84.0, 84.5, 85.0, 85.5, 86.0, 87.0, 89.0, 91.0, 93.0, 95.0]

# Weak dead-cat bounce: ret5 up a bit BUT price still below the 20d MA.
_DEADCAT = [100.0, 98.0, 95.0, 92.0, 89.0, 87.0, 86.0, 85.0, 84.0, 83.0,
            82.5, 82.0, 81.5, 81.0, 80.5, 80.0, 80.5, 81.0, 82.0, 83.0, 84.0]


def test_default_off_recovering_stays_bear():
    r, _ = _regime(_RECOVER_CHOP)  # override not enabled
    assert r == "bear", "with the override OFF, a recovering series still reads bear (no behavior change)"


def test_recovering_becomes_chop_when_on():
    r, cache = _regime(_RECOVER_CHOP, ON)
    assert r == "chop", "all 3 gates met + moderate thrust -> chop (participate)"
    assert (cache.get("_market_regime_diag") or {}).get("raw") == "recover->chop"


def test_strong_thrust_becomes_bull():
    r, _ = _regime(_RECOVER_BULL, ON)
    assert r == "bull", "very strong ret5 thrust -> bull"


def test_deepening_bear_stays_bear():
    r, _ = _regime(_DEEPENING, ON)
    assert r == "bear", "still making new lows (ret5<0, below MA) -> override cannot fire"


def test_deadcat_bounce_stays_bear():
    # ret5 is positive (a bounce) but price is still below the 20d MA -> gate 2 fails.
    r, cache = _regime(_DEADCAT, ON)
    assert r == "bear", "a weak bounce that fails the MA-reclaim gate must stay bear"
    assert "recovery" not in (cache.get("_market_regime_diag") or {})


def test_thresholds_configurable():
    # Raising the ret5 requirement above the actual thrust blocks the override.
    r, _ = _regime(_RECOVER_CHOP, dict(ON, regime_recovery_ret5_min_pct=6.0))
    assert r == "bear", "if ret5 requirement > actual thrust, override does not fire"
    # Raising the off-low requirement past the actual recovery blocks it too.
    r2, _ = _regime(_RECOVER_CHOP, dict(ON, regime_recovery_off_low_pct=0.99, regime_recovery_ret5_min_pct=2.0))
    assert r2 in ("bear",), "if recovery-depth requirement not met, override does not fire"
