"""Discovery-expansion fix (2026-05-26).

Repair the funnel that backfired in backtest 404780 (+152% vs +266% baseline):
exclude leveraged/inverse/commodity ETFs from momentum EQUITY discovery, and
concentrate the buy-side defaults while keeping discovery breadth wide.

Spec:  docs/superpowers/specs/2026-05-26-discovery-expansion-fix-design.md
Plan:  docs/superpowers/plans/2026-05-26-discovery-expansion-fix.md

Real asserts (these actually guard behavior), mirroring
test_nexus_discovery_expansion.py.
"""
from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
_BACKEND = os.path.abspath(os.path.join(_HERE, ".."))
for _p in (_BACKEND, _ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from strategies import graph_nexus_analysis as gna  # noqa: E402


# ── ETF exclusion from momentum equity discovery ─────────────────────────────

def test_leveraged_inverse_set_covers_404780_offenders():
    s = gna._LEVERAGED_INVERSE_ETF_TICKERS
    for t in ("SOXS", "OILD", "BOIL", "KOLD", "COPX", "COPZ", "CPER", "KCOP", "SLVX"):
        assert t in s, f"{t} missing from leveraged/inverse set"


def test_momentum_excluded_set_is_union_with_commodity():
    excl = gna._MOMENTUM_EXCLUDED_ETF_TICKERS
    assert set(gna._COMMODITY_ETF_TICKERS) <= excl
    assert set(gna._LEVERAGED_INVERSE_ETF_TICKERS) <= excl
    # plain equities must NOT be excluded
    for t in ("INTC", "ICHR", "NGD", "GEV", "SLAB"):
        assert t not in excl


def test_filter_drops_leveraged_etfs_when_enabled():
    cands = [("INTC", 25.0, 50.0), ("SOXS", 30.0, 10.0), ("ICHR", 24.0, 41.0), ("BOIL", 40.0, 5.0)]
    out = gna._filter_momentum_etf_candidates(cands, {"momentum_discovery_exclude_leveraged_etfs": True})
    assert [c[0] for c in out] == ["INTC", "ICHR"]


def test_filter_noop_when_disabled():
    cands = [("INTC", 25.0, 50.0), ("SOXS", 30.0, 10.0)]
    out = gna._filter_momentum_etf_candidates(cands, {"momentum_discovery_exclude_leveraged_etfs": False})
    assert [c[0] for c in out] == ["INTC", "SOXS"]


def test_filter_default_excludes_when_key_absent():
    # Default must be ON (gate defaults true).
    cands = [("INTC", 25.0, 50.0), ("OILD", 30.0, 10.0)]
    out = gna._filter_momentum_etf_candidates(cands, {})
    assert [c[0] for c in out] == ["INTC"]


# ── Concentrated buy-side defaults (discovery stays wide) ─────────────────────

def test_effective_config_concentrated_buyside_defaults():
    eff = gna._get_effective_nexus_config({})
    assert eff["pool_a_base"] == 10
    assert eff["pool_b_base"] == 4
    assert eff["llm_overlay_max_stock_candidates"] == 30
    assert eff["max_stock_buys_per_day"] == 8
    # discovery breadth stays wide
    assert eff["max_discovered_stocks"] == 90
    assert eff["momentum_discovery_max_per_day"] == 6
    # new gate defaults on
    assert eff["momentum_discovery_exclude_leveraged_etfs"] is True


# ── Shared exclusion predicate (used by discovery, rediscovery, watchlist) ────

def test_is_excluded_momentum_etf_true_for_leveraged_and_commodity():
    cfg = {"momentum_discovery_exclude_leveraged_etfs": True}
    for t in ("SOXS", "OILD", "BOIL", "PSLV", "UNG", "COPX"):
        assert gna._is_excluded_momentum_etf(t, cfg) is True, t


def test_is_excluded_momentum_etf_false_for_equities():
    cfg = {"momentum_discovery_exclude_leveraged_etfs": True}
    for t in ("INTC", "ICHR", "NGD", "GEV", "slab"):  # case-insensitive
        assert gna._is_excluded_momentum_etf(t, cfg) is False, t


def test_is_excluded_momentum_etf_respects_gate_off():
    assert gna._is_excluded_momentum_etf("SOXS", {"momentum_discovery_exclude_leveraged_etfs": False}) is False


def test_is_excluded_momentum_etf_default_on_when_key_absent():
    assert gna._is_excluded_momentum_etf("OILD", {}) is True


def test_momentum_watchlist_excludes_leveraged_etfs():
    # _build_momentum_watchlist takes no DB — pure dict accumulation.
    cache: dict = {}
    wl = gna._build_momentum_watchlist(
        cache,
        ["INTC", "SOXS", "ICHR", "BOIL"],
        config={"momentum_discovery_exclude_leveraged_etfs": True},
    )
    assert "INTC" in wl and "ICHR" in wl
    assert "SOXS" not in wl and "BOIL" not in wl


def test_momentum_watchlist_keeps_etfs_when_gate_off():
    cache: dict = {}
    wl = gna._build_momentum_watchlist(
        cache,
        ["INTC", "SOXS"],
        config={"momentum_discovery_exclude_leveraged_etfs": False},
    )
    assert "INTC" in wl and "SOXS" in wl
