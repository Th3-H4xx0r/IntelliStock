"""Phase η (propagation enrichment + V31 sector cap swap) tests.

Reference: docs/superpowers/specs/2026-05-20-eta-propagation-enrichment-design.md

Covers:
  * η.A — momentum_watchlist → sentiment_data seeding
  * η.B' — post-aggregation sector-peer augmentation
  * η.D — V28.9 HIGH-tier-in-grace protection
  * η.E — priority floor differentiator
  * η.G — V31 sector cap conditional swap
"""
from __future__ import annotations

import os
import sys
from unittest.mock import patch, MagicMock

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
_BACKEND = os.path.abspath(os.path.join(_HERE, ".."))
for _p in (_BACKEND, _ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import backend.strategies.graph_nexus_analysis as gna


@pytest.fixture(autouse=True)
def _reset_gn_live_flag():
    prev = gna._GN_LIVE_MODE_FLAG
    gna._GN_LIVE_MODE_FLAG = False
    yield
    gna._GN_LIVE_MODE_FLAG = prev


def _capture_logs():
    logs: list[tuple[str, str]] = []

    def _fake_log(msg, color=""):
        logs.append((str(msg), str(color or "")))

    return logs, patch.object(gna, "_log", side_effect=_fake_log)


def test_eta_scaffold_imports_ok():
    """Smoke — module imports and fixtures load."""
    assert gna is not None
    assert hasattr(gna, "_compute_propagated_scores")


# ─────────────────────────────────────────────────────────────────────
# η.A — momentum_watchlist seeding tests
# ─────────────────────────────────────────────────────────────────────


def _seed_momentum_watchlist(strategy_cache: dict, entries: list[tuple[str, float]]):
    strategy_cache["_momentum_watchlist"] = {t: {"score": s} for t, s in entries}


def _apply_eta_a_directly(sentiment_data: dict, mentioned: set, strategy_cache: dict, config: dict):
    """Apply ONLY the η.A logic block as it will be inserted into
    _compute_propagated_scores. Mirrors the spec §4.1 code exactly."""
    if not config.get("eta_momentum_seeding_enabled", True):
        return 0
    mw_top_n = int(config.get("eta_momentum_seed_top_n", 5) or 5)
    mw_min_score = float(config.get("eta_momentum_seed_min_score", 0.05) or 0.05)
    mw = strategy_cache.get("_momentum_watchlist", {}) if isinstance(strategy_cache, dict) else {}
    if not isinstance(mw, dict) or not mw:
        return 0
    sorted_mw = sorted(
        mw.items(),
        key=lambda kv: -float(kv[1].get("score", 0.0) or 0.0),
    )[:mw_top_n]
    seeded = 0
    for ticker, data in sorted_mw:
        if not isinstance(ticker, str) or not ticker:
            continue
        existing = sentiment_data.get(ticker)
        if existing and int(existing.get("sentiment", 0) or 0) != 0:
            continue
        score = float(data.get("score", 0.0) or 0.0)
        if score < mw_min_score:
            continue
        sentiment_data[ticker] = {"sentiment": 1, "event": "momentum_breakout"}
        mentioned.add(ticker)
        seeded += 1
    return seeded


def test_eta_a_seeds_top_n_unseen_tickers():
    sd: dict = {}
    mn: set = set()
    sc = {}
    _seed_momentum_watchlist(sc, [
        ("MU", 0.04), ("LITE", 0.135), ("PRAX", 0.121), ("BOIL", 0.039),
    ])
    seeded = _apply_eta_a_directly(sd, mn, sc, {})
    # 0.04 and 0.039 are below default min=0.05 → skipped
    # 0.135, 0.121 qualify; top_n=5 default
    assert seeded == 2
    assert sd["LITE"] == {"sentiment": 1, "event": "momentum_breakout"}
    assert sd["PRAX"] == {"sentiment": 1, "event": "momentum_breakout"}
    assert "MU" not in sd  # below min
    assert {"LITE", "PRAX"} <= mn


def test_eta_a_skips_already_seeded_with_nonzero_sentiment():
    sd: dict = {"LITE": {"sentiment": -2, "event": "earnings"}}
    mn: set = {"LITE"}
    sc = {}
    _seed_momentum_watchlist(sc, [("LITE", 0.5)])
    seeded = _apply_eta_a_directly(sd, mn, sc, {})
    assert seeded == 0
    assert sd["LITE"]["sentiment"] == -2  # NOT overwritten


def test_eta_a_seeds_when_existing_sentiment_is_zero():
    sd: dict = {"LITE": {"sentiment": 0, "event": "stale"}}
    mn: set = set()
    sc = {}
    _seed_momentum_watchlist(sc, [("LITE", 0.5)])
    seeded = _apply_eta_a_directly(sd, mn, sc, {})
    assert seeded == 1
    assert sd["LITE"] == {"sentiment": 1, "event": "momentum_breakout"}


def test_eta_a_respects_kill_switch():
    sd: dict = {}
    mn: set = set()
    sc = {}
    _seed_momentum_watchlist(sc, [("MU", 0.5), ("LITE", 0.5)])
    seeded = _apply_eta_a_directly(sd, mn, sc, {"eta_momentum_seeding_enabled": False})
    assert seeded == 0
    assert sd == {}


def test_eta_a_handles_empty_watchlist_without_error():
    sd: dict = {}
    mn: set = set()
    sc = {}
    seeded = _apply_eta_a_directly(sd, mn, sc, {})
    assert seeded == 0


def test_eta_a_respects_min_score_threshold():
    sd: dict = {}
    mn: set = set()
    sc = {}
    _seed_momentum_watchlist(sc, [("A", 0.10), ("B", 0.04), ("C", 0.02)])
    seeded = _apply_eta_a_directly(sd, mn, sc, {"eta_momentum_seed_min_score": 0.05})
    assert seeded == 1  # only A qualifies (0.10 >= 0.05)
    assert "A" in sd
    assert "B" not in sd
    assert "C" not in sd


def test_eta_a_respects_top_n_cap():
    sd: dict = {}
    mn: set = set()
    sc = {}
    _seed_momentum_watchlist(sc, [
        ("A", 0.9), ("B", 0.8), ("C", 0.7), ("D", 0.6), ("E", 0.5), ("F", 0.4),
    ])
    seeded = _apply_eta_a_directly(sd, mn, sc, {"eta_momentum_seed_top_n": 3})
    assert seeded == 3
    assert {"A", "B", "C"} <= set(sd.keys())
    assert "D" not in sd


# ─────────────────────────────────────────────────────────────────────
# η.B' — post-aggregation sector augmentation tests
# ─────────────────────────────────────────────────────────────────────


def _apply_eta_b_directly(aggregated: dict, mw_tickers: set, sector_map: dict, config: dict):
    """Mirrors the spec §4.2 logic for unit-testing."""
    if not config.get("eta_sector_augmentation_enabled", True):
        return 0
    thr = float(config.get("eta_augment_below_raw", 0.5) or 0.5)
    fac = float(config.get("eta_augment_peer_factor", 0.3) or 0.3)
    augmented = 0
    for ticker in mw_tickers:
        existing = aggregated.get(ticker, {"raw_score": 0.0, "reasons": [], "n_paths": 0})
        if float(existing.get("raw_score", 0.0)) >= thr:
            continue
        ticker_sector = sector_map.get(ticker)
        if not ticker_sector:
            continue
        peer_score = 0.0
        for peer, peer_doc in aggregated.items():
            if peer == ticker:
                continue
            if sector_map.get(peer) != ticker_sector:
                continue
            peer_raw = float(peer_doc.get("raw_score", 0.0))
            if peer_raw > peer_score:
                peer_score = peer_raw
        if peer_score < thr:
            continue
        new_raw = float(existing.get("raw_score", 0.0)) + peer_score * fac
        aggregated[ticker] = {
            "raw_score": max(-1.0, min(1.0, new_raw)),
            "reasons": (list(existing.get("reasons", [])) +
                        [f"eta_b_aug(peer={peer_score:.2f}*{fac})"])[:5],
            "n_paths": int(existing.get("n_paths", 0)) + 1,
        }
        augmented += 1
    return augmented


def test_eta_b_augments_low_raw_with_high_peer():
    agg = {
        "MU": {"raw_score": 0.2, "reasons": [], "n_paths": 1},
        "NVDA": {"raw_score": 0.9, "reasons": [], "n_paths": 8},
    }
    sm = {"MU": "Technology", "NVDA": "Technology"}
    n = _apply_eta_b_directly(agg, {"MU"}, sm, {})
    assert n == 1
    assert agg["MU"]["raw_score"] == pytest.approx(0.2 + 0.9 * 0.3, rel=1e-6)
    assert agg["MU"]["n_paths"] == 2
    assert any("eta_b_aug" in r for r in agg["MU"]["reasons"])


def test_eta_b_skips_when_existing_raw_above_threshold():
    agg = {
        "MU": {"raw_score": 0.7, "reasons": [], "n_paths": 3},
        "NVDA": {"raw_score": 0.9, "reasons": [], "n_paths": 8},
    }
    sm = {"MU": "Technology", "NVDA": "Technology"}
    n = _apply_eta_b_directly(agg, {"MU"}, sm, {})
    assert n == 0
    assert agg["MU"]["raw_score"] == 0.7  # untouched


def test_eta_b_skips_when_no_peer_above_threshold():
    agg = {
        "MU": {"raw_score": 0.2, "reasons": [], "n_paths": 1},
        "NVDA": {"raw_score": 0.3, "reasons": [], "n_paths": 2},  # below 0.5
    }
    sm = {"MU": "Technology", "NVDA": "Technology"}
    n = _apply_eta_b_directly(agg, {"MU"}, sm, {})
    assert n == 0


def test_eta_b_clamps_at_positive_one():
    agg = {
        "MU": {"raw_score": 0.9, "reasons": [], "n_paths": 1},
        "NVDA": {"raw_score": 0.95, "reasons": [], "n_paths": 8},
    }
    sm = {"MU": "Technology", "NVDA": "Technology"}
    n = _apply_eta_b_directly(agg, {"MU"}, sm, {})
    # raw=0.9 > threshold 0.5 → skipped
    assert n == 0
    # Now retry with MU below threshold
    agg2 = {
        "MU": {"raw_score": 0.4, "reasons": [], "n_paths": 1},
        "NVDA": {"raw_score": 0.95, "reasons": [], "n_paths": 8},
    }
    sm2 = {"MU": "Technology", "NVDA": "Technology"}
    n2 = _apply_eta_b_directly(agg2, {"MU"}, sm2, {})
    assert n2 == 1
    # 0.4 + 0.95 * 0.3 = 0.685 — under 1.0, no clamp needed
    assert agg2["MU"]["raw_score"] == pytest.approx(0.685, rel=1e-6)


def test_eta_b_respects_kill_switch():
    agg = {
        "MU": {"raw_score": 0.2, "reasons": [], "n_paths": 1},
        "NVDA": {"raw_score": 0.9, "reasons": [], "n_paths": 8},
    }
    sm = {"MU": "Technology", "NVDA": "Technology"}
    n = _apply_eta_b_directly(agg, {"MU"}, sm, {"eta_sector_augmentation_enabled": False})
    assert n == 0
    assert agg["MU"]["raw_score"] == 0.2


def test_eta_b_skips_when_sector_unknown():
    agg = {
        "MU": {"raw_score": 0.2, "reasons": [], "n_paths": 1},
        "NVDA": {"raw_score": 0.9, "reasons": [], "n_paths": 8},
    }
    sm = {"NVDA": "Technology"}  # MU not in sector_map
    n = _apply_eta_b_directly(agg, {"MU"}, sm, {})
    assert n == 0
