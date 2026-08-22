"""The rank band ranks on the wrong axis for a momentum breakout (bt 820236).

`_apply_rank_band` ranks on `_rotation_effective_score` -- the news/graph/ML
blend. Its own docstring already exempts ETFs and sleeve legs because "their
conviction is trend strength and regime, not the news/graph/ML blend ... ranking
them against stocks would not tighten the ETF sleeve, it would silently switch
it off". A momentum breakout is the same case.

bt 820236 refused 2,833 buy signals through this band. What it refused:

    VICR  discovered 20d=+20.4%  60d=+119.6%  -> blocked x6, never bought
    AAOI                                      -> blocked x3, never bought
    AMAT                                      -> blocked x9, never bought
    LASR  discovered 20d=+21.5%  60d= +40.9%  -> never bought

against what it admitted, which carried the run:

    WDC   discovered 20d= +7.7%  60d= +37.5%  -> +$450.49
    LRCX  discovered 20d=+15.8%  60d= +31.9%  -> +$238.22
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from strategies.graph_nexus_analysis import (  # noqa: E402
    _apply_rank_band_gate as _apply_rank_band,
)


class _Emu:
    def __init__(self, positions=None):
        self._positions = dict(positions or {})

    def get_positions(self):
        return dict(self._positions)


def _book():
    """One leader on price that sits mid-pack on the graph blend, plus filler."""
    scores = {
        "VICR": {"score": 1, "raw_net_score": 0.30, "momentum_watchlist_score": 1.25},
        "WDC": {"score": 1, "raw_net_score": 1.80, "momentum_watchlist_score": 0.90},
    }
    for i in range(18):
        scores[f"F{i:02d}"] = {"score": 0, "raw_net_score": 1.0 - i * 0.01,
                               "momentum_watchlist_score": 0.0}
    return scores


BASE = {"rank_band_enabled": True, "rank_band_entry_pct": 10.0,
        "rank_band_exit_pct": 50.0}


def test_the_bug_a_price_leader_is_refused_on_a_news_ranking():
    out = _apply_rank_band(_book(), list(_book()), _Emu(), dict(BASE))
    assert out["WDC"]["score"] == 1, "the graph-strong name still enters"
    assert out["VICR"]["score"] == 0, "the price leader is refused"
    assert "RANK_BAND" in out["VICR"]["reason"]


def test_momentum_exemption_admits_the_leader():
    cfg = dict(BASE, rank_band_momentum_exempt_min_score=1.0)
    out = _apply_rank_band(_book(), list(_book()), _Emu(), cfg)
    assert out["VICR"]["score"] == 1
    assert "RANK_BAND" not in str(out["VICR"].get("reason") or "")


def test_default_off_is_byte_identical():
    a = _apply_rank_band(_book(), list(_book()), _Emu(), dict(BASE))
    b = _apply_rank_band(_book(), list(_book()), _Emu(),
                         dict(BASE, rank_band_momentum_exempt_min_score=0.0))
    assert a["VICR"]["score"] == b["VICR"]["score"] == 0


def test_a_weak_momentum_name_is_still_refused():
    """The exemption is for leaders, not an off switch for the band."""
    scores = _book()
    scores["VICR"]["momentum_watchlist_score"] = 0.40
    cfg = dict(BASE, rank_band_momentum_exempt_min_score=1.0)
    out = _apply_rank_band(scores, list(scores), _Emu(), cfg)
    assert out["VICR"]["score"] == 0


def test_the_exit_band_is_untouched():
    """Scoped to ENTRY only -- the buy/hold spread still governs leaving."""
    scores = _book()
    scores["WDC"] = {"score": -1, "raw_net_score": 1.80,
                     "momentum_watchlist_score": 1.50}
    cfg = dict(BASE, rank_band_momentum_exempt_min_score=1.0)
    out = _apply_rank_band(scores, list(scores), _Emu({"WDC": 5.0}), cfg)
    # still inside the hold band, so the signal sell is still suppressed
    assert out["WDC"]["score"] == 0
    assert "RANK_BAND" in out["WDC"]["reason"]


def test_a_protective_exit_is_never_gated():
    scores = _book()
    scores["WDC"] = {"score": -1, "raw_net_score": 1.80,
                     "momentum_watchlist_score": 1.50, "_forced_exit": True}
    cfg = dict(BASE, rank_band_momentum_exempt_min_score=1.0)
    out = _apply_rank_band(scores, list(scores), _Emu({"WDC": 5.0}), cfg)
    assert out["WDC"]["score"] == -1


def test_band_disabled_short_circuits():
    cfg = dict(BASE, rank_band_enabled=False,
               rank_band_momentum_exempt_min_score=1.0)
    out = _apply_rank_band(_book(), list(_book()), _Emu(), cfg)
    assert out["VICR"]["score"] == 1 and out["WDC"]["score"] == 1


def test_missing_or_malformed_momentum_score_does_not_exempt():
    scores = _book()
    scores["VICR"].pop("momentum_watchlist_score")
    cfg = dict(BASE, rank_band_momentum_exempt_min_score=1.0)
    assert _apply_rank_band(scores, list(scores), _Emu(), cfg)["VICR"]["score"] == 0

    scores2 = _book()
    scores2["VICR"]["momentum_watchlist_score"] = "not-a-number"
    assert _apply_rank_band(scores2, list(scores2), _Emu(), cfg)["VICR"]["score"] == 0


def test_ranked_cache_fallback_reaches_discovery_lane_names():
    """2026-08-22 wiring fix (bt 443898: 0 exemptions in 44 evaluations with
    the lever armed): `momentum_watchlist_score` is stamped only on the
    watchlist lane's own picks, so a discovery-lane mover carries NO score
    field and the exemption never fired for exactly the names it was written
    for. `_momentum_ranked_cache` (full momentum ranking, prior bar) is the
    fallback."""
    scores = _book()
    # A discovery-lane mover: buy signal, mid-pack blend, NO watchlist stamp.
    scores["AAOI"] = {"score": 1, "raw_net_score": 0.25}
    cache = {"_momentum_ranked_cache": [("AAOI", 1.4), ("VICR", 1.25)]}
    cfg = dict(BASE, rank_band_momentum_exempt_min_score=1.0)
    out = _apply_rank_band(scores, list(scores), _Emu(), cfg,
                           strategy_cache=cache)
    assert out["AAOI"]["score"] == 1, "ranked-cache fallback must exempt it"
    assert "RANK_BAND" not in str(out["AAOI"].get("reason") or "")


def test_ranked_cache_fallback_does_not_rescue_weak_names():
    """Anti-vacuity: a discovery name below the exemption floor stays blocked
    even when it appears in the ranked cache."""
    scores = _book()
    scores["WEAK"] = {"score": 1, "raw_net_score": 0.20}
    cache = {"_momentum_ranked_cache": [("WEAK", 0.4)]}
    cfg = dict(BASE, rank_band_momentum_exempt_min_score=1.0)
    out = _apply_rank_band(scores, list(scores), _Emu(), cfg,
                           strategy_cache=cache)
    assert out["WEAK"]["score"] == 0
    assert "RANK_BAND" in out["WEAK"]["reason"]


def test_no_cache_and_no_stamp_behaves_as_before():
    """Without a strategy_cache the fallback is empty and the pre-fix
    behaviour is byte-identical (the stamped-score path still works)."""
    scores = _book()
    scores["AAOI"] = {"score": 1, "raw_net_score": 0.25}
    cfg = dict(BASE, rank_band_momentum_exempt_min_score=1.0)
    out = _apply_rank_band(scores, list(scores), _Emu(), cfg)
    assert out["AAOI"]["score"] == 0, "no stamp + no cache = still blocked"
    assert out["VICR"]["score"] == 1, "stamped path unchanged"


def test_stamped_score_wins_over_ranked_cache():
    """A positive stamped score is authoritative; the cache is only a
    fallback for names the watchlist lane never stamped."""
    scores = _book()
    scores["MIXD"] = {"score": 1, "raw_net_score": 0.22,
                      "momentum_watchlist_score": 0.5}
    cache = {"_momentum_ranked_cache": [("MIXD", 2.0)]}
    cfg = dict(BASE, rank_band_momentum_exempt_min_score=1.0)
    out = _apply_rank_band(scores, list(scores), _Emu(), cfg,
                           strategy_cache=cache)
    assert out["MIXD"]["score"] == 0, "stamped value is authoritative"
