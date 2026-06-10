"""Discovery expansion (2026-05-25).

Widen the monitored universe + downstream buy funnel, accelerate momentum
capture, and protect recently momentum-discovered rows from premature eviction
so high-momentum movers stay monitored through their pre-breakout phase.

Spec: docs/superpowers/specs/2026-05-25-discovery-expansion-design.md

Unlike the legacy check()-based diagnostics in test_nexus_fixes.py, these use
real asserts so they actually guard the behavior.
"""
from __future__ import annotations

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
_BACKEND = os.path.abspath(os.path.join(_HERE, ".."))
for _p in (_BACKEND, _ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from strategies import graph_nexus_analysis as gna  # noqa: E402


def _doc(ticker, source, discovered_date, propagation_score=0.0, last_signal_date=None):
    return {
        "ticker": ticker,
        "source": source,
        "discovered_date": discovered_date,
        "last_signal_date": last_signal_date or discovered_date,
        "propagation_score": propagation_score,
        "status": "active",
    }


# ── 2A: momentum retention protection ───────────────────────────────────────

def test_is_momentum_protected_recent_true():
    assert gna._is_momentum_protected(_doc("AAOI", "momentum", "2026-01-10"), "2026-01-15", 10) is True


def test_is_momentum_protected_boundary_inclusive():
    # discovered exactly protect_days ago is still protected.
    assert gna._is_momentum_protected(_doc("AAOI", "momentum", "2026-01-05"), "2026-01-15", 10) is True


def test_is_momentum_protected_old_false():
    assert gna._is_momentum_protected(_doc("AAOI", "momentum", "2026-01-01"), "2026-01-20", 10) is False


def test_is_momentum_protected_non_momentum_false():
    assert gna._is_momentum_protected(_doc("AAOI", "trend", "2026-01-14"), "2026-01-15", 10) is False


def test_is_momentum_protected_zero_window_false():
    assert gna._is_momentum_protected(_doc("AAOI", "momentum", "2026-01-14"), "2026-01-15", 0) is False


def test_is_momentum_protected_missing_date_false():
    assert gna._is_momentum_protected(_doc("AAOI", "momentum", "2026-01-14"), None, 10) is False


def test_recent_momentum_outranks_nonmomentum_in_retention():
    # Recent momentum row (low score) must be retained over a non-momentum row.
    momentum = _doc("AAOI", "momentum", "2026-01-14", propagation_score=0.0)
    other = _doc("ZZZZ", "propagation", "2026-01-14", propagation_score=5.0)
    ordered = gna._sort_discovered_docs_for_retention(
        [other, momentum], date_key="2026-01-15", protect_days=10
    )
    assert ordered[0]["ticker"] == "AAOI"


def test_recent_momentum_beats_old_momentum_in_retention():
    # Protection is time-bounded: a recent momentum row outranks an older one
    # even though the old one has a higher propagation score.
    recent = _doc("NEW", "momentum", "2026-01-14", propagation_score=0.0)
    old = _doc("OLD", "momentum", "2026-01-01", propagation_score=5.0)
    ordered = gna._sort_discovered_docs_for_retention(
        [old, recent], date_key="2026-01-20", protect_days=10
    )
    assert ordered[0]["ticker"] == "NEW"


def test_retention_sort_no_protection_params_uses_score_order():
    # Backward compatibility: callers passing no date_key/protect_days get the
    # legacy behavior (higher propagation score retained first).
    lo = _doc("LO", "momentum", "2026-01-14", propagation_score=0.0)
    hi = _doc("HI", "momentum", "2026-01-14", propagation_score=5.0)
    ordered = gna._sort_discovered_docs_for_retention([lo, hi])
    assert ordered[0]["ticker"] == "HI"


def test_trim_selection_shields_protected_momentum_over_cap():
    # The cap trim must NOT select protected momentum rows even when they alone
    # exceed the cap (the shield holds; the pool may transiently exceed the cap).
    docs = [
        _doc("MOM1", "momentum", "2026-01-14"),
        _doc("MOM2", "momentum", "2026-01-14"),
        _doc("MOM3", "momentum", "2026-01-14"),
    ]
    to_trim = gna._select_discovered_to_trim(docs, 1, date_key="2026-01-15", protect_days=10)
    assert to_trim == []


def test_trim_selection_trims_unprotected_past_cap():
    docs = [
        _doc("MOM1", "momentum", "2026-01-14"),               # protected → retained
        _doc("OTH1", "trend", "2026-01-14", propagation_score=9.0),
        _doc("OTH2", "trend", "2026-01-14", propagation_score=0.0),
    ]
    to_trim = gna._select_discovered_to_trim(docs, 1, date_key="2026-01-15", protect_days=10)
    assert {d["ticker"] for d in to_trim} == {"OTH1", "OTH2"}


def test_trim_selection_old_momentum_not_shielded():
    docs = [
        _doc("RECENT", "momentum", "2026-01-18"),  # within window → protected
        _doc("OLDMOM", "momentum", "2026-01-01"),  # >10d old → not protected → trimmable
    ]
    to_trim = gna._select_discovered_to_trim(docs, 1, date_key="2026-01-20", protect_days=10)
    assert {d["ticker"] for d in to_trim} == {"OLDMOM"}


# ── 2B: sector-fill candidate determinism ───────────────────────────────────

def test_sector_fill_candidates_deterministic_and_sorted():
    out1 = gna._sector_fill_candidates_for_sector(
        {"Semiconductors": ["NVDA", "AMD", "INTC", "MU"]}, "Semiconductors", set()
    )
    out2 = gna._sector_fill_candidates_for_sector(
        {"Semiconductors": ["MU", "INTC", "AMD", "NVDA"]}, "Semiconductors", set()
    )
    assert out1 == out2 == ["AMD", "INTC", "MU", "NVDA"]


def test_sector_fill_candidates_respects_exclusions():
    out = gna._sector_fill_candidates_for_sector(
        {"Semiconductors": ["NVDA", "AMD", "INTC"]}, "Semiconductors", {"AMD"}
    )
    assert out == ["INTC", "NVDA"]


# ── Expansion config defaults ────────────────────────────────────────────────

def test_effective_config_expansion_defaults():
    cfg = gna._get_effective_nexus_config({})
    assert cfg["max_discovered_stocks"] == 90
    assert cfg["pool_a_base"] == 10
    assert cfg["pool_b_base"] == 4
    assert cfg["max_stock_buys_per_day"] == 8
    assert cfg["allocation_max_new_stock_buys"] == 6
    assert cfg["momentum_discovery_max_per_day"] == 6


def _schema_config() -> dict:
    schema_path = os.path.join(_BACKEND, "strategies", "graph_nexus_analysis.py")
    with open(schema_path, "r", encoding="utf-8") as f:
        line1 = f.readline()
    cfg_start = line1.index('"config":') + len('"config":')
    depth = 0
    cfg_json = ""
    for i, ch in enumerate(line1[cfg_start:]):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                cfg_json = line1[cfg_start:cfg_start + i + 1]
                break
    return json.loads(cfg_json)


def test_schema_template_expansion_defaults():
    schema = _schema_config()
    assert schema["max_discovered_stocks"] == 90
    assert schema["llm_overlay_max_stock_candidates"] == 30
    assert schema["pool_a_base"] == 10
    assert schema["pool_b_base"] == 4
    assert schema["max_stock_buys_per_day"] == 8
    assert schema["allocation_max_new_stock_buys"] == 6
    assert schema["momentum_discovery_max_per_day"] == 6
    assert schema["momentum_discovery_min_20d_return"] == 15.0
    assert schema["momentum_discovery_min_60d_return"] == 40.0
    assert schema["momentum_discovery_protect_days"] == 10
    assert schema["momentum_discovery_exclude_leveraged_etfs"] is True
