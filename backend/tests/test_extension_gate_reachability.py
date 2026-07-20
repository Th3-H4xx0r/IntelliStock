"""Phase 1 (2026-07-20 bull-alpha): the entry-extension gate must be able to
see prices for age-0 discovery entries. Root cause (bt 148462 forensics): the
momentum lanes pass the broker `data` dict, which is EMPTY for a symbol the
broker only fetches AFTER the buy is emitted (CAR), while the momentum scorer
that PICKED the symbol reads strategy_cache["_overlay_bars_raw"] — which has
the bars. The gate fell open on missing data and never once fired. This wires
an as-of-correct fallback to the overlay cache (no lookahead) and an optional
fail-closed posture mirroring the Bear RS gate.
"""
import os
import sys

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

import strategies.graph_nexus_analysis as g


def _ohlc(pairs):
    """[(date, close), ...] -> overlay-cache bar shape {"t":.., "c":..}."""
    return [{"t": d, "c": c} for d, c in pairs]


# --- _resolve_asof_bars -----------------------------------------------------

def test_resolve_prefers_broker_bars():
    ph = {"AAA": [{"close": 10.0}, {"close": 11.0}]}
    out = g._resolve_asof_bars("AAA", ph, {"_overlay_bars_raw": {"AAA": _ohlc([("2026-01-01", 99)])}}, "2026-04-13")
    assert out == ph["AAA"], "broker price_history wins when it has >= min_bars"


def test_resolve_falls_back_to_overlay_when_broker_empty():
    overlay = {"_overlay_bars_raw": {"CAR": _ohlc([("2026-04-10", 148.0), ("2026-04-13", 311.0)])}}
    out = g._resolve_asof_bars("CAR", {}, overlay, "2026-04-13")
    assert len(out) == 2 and out[-1]["c"] == 311.0


def test_resolve_filters_future_bars_no_lookahead():
    overlay = {"_overlay_bars_raw": {"CAR": _ohlc([
        ("2026-04-10", 148.0), ("2026-04-13", 311.0),
        ("2026-04-20", 752.0),  # future relative to the as-of date — must be dropped
    ])}}
    out = g._resolve_asof_bars("CAR", {}, overlay, "2026-04-13")
    assert [b["t"] for b in out] == ["2026-04-10", "2026-04-13"], "future bars must be filtered"


def test_resolve_empty_when_nothing_available():
    assert g._resolve_asof_bars("ZZZ", {}, {}, "2026-04-13") == []


# --- gate reachability through the momentum lane ----------------------------

def _cfg(**kw):
    base = {"entry_extension_block_pct": 25.0, "portfolio_swap_ath_gate_enabled": False,
            "momentum_watchlist_mcap_prefilter_enabled": False}
    base.update(kw)
    return base


def test_gate_fires_via_overlay_when_broker_data_empty():
    # CAR: age-0 discovery, no broker bars, but overlay has the parabolic runup.
    overlay = {"_overlay_bars_raw": {"CAR": _ohlc(
        [("2026-04-10", 148.0)] + [("2026-04-13", 311.0)])}}
    blocked, why = g._v32_momentum_ath_or_mcap_block(
        "CAR", 311.0, {}, overlay, _cfg(), 1.839, lane="mw_rotation",
        date_key="2026-04-13")
    assert blocked and "extension" in why, "gate must see overlay bars and block the +110% runup"


def test_gate_no_lookahead_uses_only_asof_bars():
    # Same symbol, but as-of the FIRST day the runup hasn't happened yet.
    overlay = {"_overlay_bars_raw": {"CAR": _ohlc([
        ("2026-04-10", 300.0), ("2026-04-13", 311.0), ("2026-04-20", 752.0)])}}
    blocked, _ = g._v32_momentum_ath_or_mcap_block(
        "CAR", 311.0, {}, overlay, _cfg(), 1.839, lane="mw_rotation",
        date_key="2026-04-10")
    assert not blocked, "as-of 04-10 only one bar visible → cannot claim a runup (no future peek)"


def test_gate_fail_closed_on_missing_bars_when_required():
    blocked, why = g._v32_momentum_ath_or_mcap_block(
        "GHOST", 50.0, {}, {}, _cfg(entry_extension_require_bars=True), 2.0,
        lane="mw_buy", date_key="2026-04-13")
    assert blocked and "no_bars" in why


def test_gate_fail_open_on_missing_bars_by_default():
    # Backward-compat: without the require flag, missing bars do NOT block.
    blocked, _ = g._v32_momentum_ath_or_mcap_block(
        "GHOST", 50.0, {}, {}, _cfg(), 2.0, lane="mw_buy", date_key="2026-04-13")
    assert not blocked


def test_lookback_scaled_to_granularity():
    # 20 bars tuned at 3600s becomes 60 bars at 1200s (same wall-clock window).
    assert g._scale_bars(20, {"_resolved_time_increment_sec": 1200}) == 60
    # With hourly cadence the raw 20 is unchanged.
    assert g._scale_bars(20, {"_resolved_time_increment_sec": 3600}) == 20
