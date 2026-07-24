"""Extension-blocked tracking through a bear-classified V-recovery (2026-07-25).

Root cause this covers: V31 confirms `bear` for ~8 sessions AFTER a V-bottom
(ret20 lags the price bottom), so the names that LEAD the recovery are
extension-blocked while the regime still reads bear. The existing tracker
(`momentum_watchlist_track_extension_blocked`) refuses to record in bear/crash,
so such a name is dropped from discovery entirely and is never priced/scored
again — it can only be re-found later, at the top.

Observed on bt#211684 (2026-03-02..04-27): CAR was extension-blocked exactly
once, on 2026-04-07 with `V31 market regime: bear`. The next session (04-08) was
chop and 04-13 was bull, where the bull-tuned static run ranked CAR #1 and
bought it at $311. The auto-switch run instead re-found CAR at $733 and lost 40%.

`momentum_watchlist_track_extension_blocked_in_bear` (default OFF) lets the
tracker RECORD in bear/crash, while the injection site still withholds every
tracked name for as long as the regime is bear/crash — so the reserved
momentum-buy lane can never knife-catch a fading rally in a real downtrend.
"""
import os
import sys

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

import strategies.graph_nexus_analysis as g


def _hist(closes):
    return [{"close": c} for c in closes]


# A name that ran +166.7% over the lookback — CAR's actual 04-07 shape.
# _recent_runup_protect measures hi/lo across the LAST `lookback_bars` (20)
# bars, so the base must sit inside that window, not before it.
_RUNUP = [100.0] * 5 + [266.7] * 15


def _block(regime, cfg=None):
    """Run the quality filter on an over-extended NEW entry and return the cache."""
    scores = {"CAR": {"score": 1, "action_intent": "initial_buy",
                      "reason": "r", "raw_net_score": 0.5}}
    cache = {"_market_regime": regime}
    conf = {"quality_filter_missing_metadata_policy": "warn",
            "entry_extension_block_pct": 25.0,
            "bear_entry_rs_filter_enabled": False,
            "momentum_watchlist_track_extension_blocked": True}
    conf.update(cfg or {})
    out = g._apply_quality_filter(
        scores, ["CAR"], {"CAR": _RUNUP[-1]}, {"CAR": _hist(_RUNUP)}, None,
        conf, strategy_cache=cache, date_key="2026-04-07",
    )
    return out["CAR"], cache


def test_extension_gate_still_blocks_the_buy_in_every_mode():
    """The tracker is evaluation-only: the BUY stays blocked in all variants."""
    for cfg in ({}, {"momentum_watchlist_track_extension_blocked_in_bear": True}):
        for regime in ("bear", "crash", "chop", "bull"):
            sc, _ = _block(regime, cfg)
            assert sc["score"] == 0, f"regime={regime} cfg={cfg} must not buy"
            assert "xtension" in sc["reason"]


def test_chop_and_bull_track_today_unchanged():
    """Existing validated behavior: chop/bull record without the new flag."""
    for regime in ("chop", "bull"):
        _, cache = _block(regime)
        assert "CAR" in (cache.get("_extension_blocked_track") or {}), regime


def test_bear_does_not_track_by_default():
    """Byte-identical to the validated feature when the new flag is unset."""
    for regime in ("bear", "crash"):
        _, cache = _block(regime)
        assert not (cache.get("_extension_blocked_track") or {}), regime


def test_bear_tracks_when_flag_enabled():
    """THE FIX: a name blocked during a bear-classified recovery stays tracked."""
    for regime in ("bear", "crash"):
        _, cache = _block(regime,
                          {"momentum_watchlist_track_extension_blocked_in_bear": True})
        assert "CAR" in (cache.get("_extension_blocked_track") or {}), regime


def test_flag_alone_does_nothing_without_the_base_lever():
    """The new flag must not resurrect tracking when the feature is off."""
    _, cache = _block("bear", {"momentum_watchlist_track_extension_blocked": False,
                               "momentum_watchlist_track_extension_blocked_in_bear": True})
    assert not (cache.get("_extension_blocked_track") or {})


def test_recording_is_recency_ordered_and_capped():
    """Cap keeps the newest entries so a long bear can't unbound the dict."""
    cache = {"_market_regime": "bear"}
    conf = {"quality_filter_missing_metadata_policy": "warn",
            "entry_extension_block_pct": 25.0,
            "bear_entry_rs_filter_enabled": False,
            "momentum_watchlist_track_extension_blocked": True,
            "momentum_watchlist_track_extension_blocked_in_bear": True,
            "momentum_watchlist_track_extension_blocked_max": 3}
    for sym in ("AAA", "BBB", "CCC", "DDD"):
        g._apply_quality_filter(
            {sym: {"score": 1, "action_intent": "initial_buy", "reason": "r",
                   "raw_net_score": 0.5}},
            [sym], {sym: _RUNUP[-1]}, {sym: _hist(_RUNUP)}, None,
            dict(conf), strategy_cache=cache, date_key="2026-04-07")
    tracked = cache.get("_extension_blocked_track") or {}
    assert list(tracked.keys()) == ["BBB", "CCC", "DDD"], tracked
