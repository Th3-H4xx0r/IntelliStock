"""V31 regime detector hardening (2026-07-19 regime-safety spec, Phase 1c).

The detector must pick a proxy by USABLE point-in-time closes (not "has any
bars"), fall back to the engine-supplied `data` bars when the overlay cache
is blind, and fail safe to chop — loudly — instead of silently bull.
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


def _flat(start: str, n: int, close: float = 100.0):
    return _bars(start, [close] * n)


def test_proxy_skips_unusable_bars():
    # QQQ has bars but ALL after date_key (the 2026-07-19 shadowing bug);
    # VOO has 60 usable flat closes → detector must use VOO, not fall back.
    cache = {"_overlay_bars_raw": {
        "QQQ": _flat("2026-04-30", 53),
        "VOO": _flat("2025-12-01", 60),
    }}
    regime = g._detect_market_regime(cache, {}, "2026-03-02")
    assert regime in ("bull", "chop", "bear")
    diag = cache.get("_market_regime_diag") or {}
    assert diag.get("proxy") == "VOO"
    assert diag.get("closes", 0) >= 21


def test_blind_falls_back_to_chop_not_bull():
    cache = {"_overlay_bars_raw": {}}
    assert g._detect_market_regime(cache, {}, "2026-03-02") == "chop"
    diag = cache.get("_market_regime_diag") or {}
    assert diag.get("proxy") is None


def test_blind_fallback_config_override():
    cache = {"_overlay_bars_raw": {}}
    cfg = {"regime_blind_fallback": "bull"}
    assert g._detect_market_regime(cache, cfg, "2026-03-02") == "bull"


def test_data_param_fallback():
    # Overlay cache blind, but the engine's own bar universe has SPY bars
    # (hourly; multiple bars per day must resample to daily closes).
    hourly = []
    base = datetime(2025, 12, 1)
    for d in range(40):
        for h in (15, 16, 17):
            hourly.append({"t": (base + timedelta(days=d, hours=h)).strftime("%Y-%m-%dT%H:00:00Z"),
                           "c": 100.0})
    cache = {"_overlay_bars_raw": {}}
    regime = g._detect_market_regime(cache, {}, "2026-01-05",
                                     data={"SPY": {"bars": hourly}})
    diag = cache.get("_market_regime_diag") or {}
    assert diag.get("proxy") == "SPY(data)"
    assert regime in ("bull", "chop", "bear")


def test_bear_on_20d_drawdown():
    closes = [100.0] * 45 + [93.0] * 16  # closes[-21]=100, current=93 → ret20=-7%
    cache = {"_overlay_bars_raw": {"SPY": _bars("2025-12-01", closes)}}
    assert g._detect_market_regime(cache, {}, "2026-02-15") == "bear"


def test_bull_on_uptrend():
    closes = [100.0 + i * 0.5 for i in range(60)]
    cache = {"_overlay_bars_raw": {"SPY": _bars("2025-12-01", closes)}}
    assert g._detect_market_regime(cache, {}, "2026-02-15") == "bull"
