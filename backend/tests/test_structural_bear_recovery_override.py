"""Structural (<200d MA) bear must be recovery-overridable (2026-07-25).

THE BUG: `_detect_market_regime` returns bear from the structural branch
`len(closes) >= 200 and current < ma_200` BEFORE the bull check and BEFORE the
bull-zone recovery override. On a window with <200 proxy closes that branch is
unreachable, so the SAME tape falls through to the recovery override and reads
chop. Regime therefore depends on how much history the run happens to hold.

Measured on real runs, same calendar days, identical ret20:
    2026-04-06  ret20=-2.02  ->  bear (bt#211684, closes=303)
                             ->  chop (bt#353454, closes=76)
    2026-04-07  ret20=-2.81  ->  bear (bt#211684, closes=304)
                             ->  chop (bt#353454, closes=77)

ret20 is above the -3 bear threshold on both days, so the ret20 branch (which IS
overridable) never fires — only the structural one does. Consequences under
bear: max_positions 14->2, the bear RS gate blocks entries, and the extension
gate deletes the recovery leaders from discovery.

`regime_structural_bear_recovery_override_enabled` (default OFF) lets the SAME
`_recovery_override_regime` guard that already protects the ret20 branch also
protect the structural one — identical dead-cat filtering (ret5 thrust +
short-MA reclaim + off-low depth), so a genuine downtrend under its 200d MA
still reads bear.
"""
import os
import sys

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

import strategies.graph_nexus_analysis as g

_CFG = {
    "regime_detector_enabled": True,
    "regime_bear_spy_drawdown_pct": 3,
    "regime_recovery_override_enabled": True,
    "regime_recovery_ma_bars": 10,
    "regime_recovery_ret5_min_pct": 1.5,
    "regime_recovery_off_low_pct": 0.5,
    "regime_recovery_bull_ret5_pct": 4,
    "regime_structural_bear_recovery_override_enabled": True,
}


def _series(recovering=True, n=260):
    """Elevated long history (so ma_200 is high), a selloff, a base, then either
    a thrust to new 20-bar highs or a continued bleed. Mirrors an April-2026 V:
    price is under its 200d MA while the short-term tape has clearly turned."""
    hist = [300.0] * (n - 40)
    decline = [300.0 - 7.5 * i for i in range(1, 21)]      # 292.5 -> 150
    base = [150.0, 152.0, 151.0, 153.0, 152.0, 154.0,
            153.0, 155.0, 154.0, 156.0, 155.0, 157.0]      # ~2wk base
    tail = ([160.0, 166.0, 172.0, 179.0, 186.0] if recovering
            else [153.0, 149.0, 145.0, 141.0, 137.0])
    return hist + decline + base + tail


def _fire(closes, cfg=None):
    diag = {}
    ret5 = ((closes[-1] - closes[-6]) / closes[-6]) * 100.0
    return g._recovery_override_regime(closes, closes[-1], ret5, cfg or _CFG, diag), diag


def test_fixture_trips_the_structural_branch():
    """The fixture must actually sit below its 200d MA with >=200 closes."""
    closes = _series()
    assert len(closes) >= 200
    assert closes[-1] < sum(closes[-200:]) / 200.0


def test_recovery_guard_fires_on_a_real_turn():
    out, diag = _fire(_series(recovering=True))
    assert out in ("chop", "bull"), (out, diag)


def test_recovery_guard_rejects_a_deepening_bear():
    """Dead-cat safety: the same guard must NOT fire on a still-falling tape."""
    out, _ = _fire(_series(recovering=False))
    assert out is None, out


def test_long_and_short_windows_agree():
    """THE INVARIANT: identical tape, different history length -> same verdict."""
    closes = _series(recovering=True)
    long_out, _ = _fire(closes)
    short_out, _ = _fire(closes[-80:])
    assert long_out == short_out, (long_out, short_out)
    assert long_out in ("chop", "bull")


def _detect(closes, cfg):
    cache = {"_overlay_bars_raw": {"SPY": [
        {"c": c, "t": "2026-01-01"} for c in closes]}}
    return g._detect_market_regime(cache, cfg, "2026-04-07")


def test_detector_default_off_still_reads_bear():
    """Default OFF keeps today's behavior: structural bear wins."""
    cfg = dict(_CFG)
    cfg["regime_structural_bear_recovery_override_enabled"] = False
    assert _detect(_series(recovering=True), cfg) == "bear"


def test_detector_enabled_overrides_structural_bear():
    """THE FIX: with the flag on, a genuine recovery lifts the structural bear."""
    assert _detect(_series(recovering=True), _CFG) in ("chop", "bull")


def test_detector_enabled_still_bear_on_a_deepening_tape():
    """The flag must not disarm bear protection in a real downtrend."""
    assert _detect(_series(recovering=False), _CFG) == "bear"
