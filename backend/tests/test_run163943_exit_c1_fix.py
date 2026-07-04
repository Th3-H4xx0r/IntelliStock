"""Tests for the run-163943 forensics fixes:
  - Track C: `_recent_runup_protect` shared guard + wiring (FLC refactor, V28.9
    losing break-glass). Guard is opt-in / default-off (behavior-preserving).
  - Track A: `_rotation_incoming_sector_cap_ok` rotation sell-leg pre-validation
    (conservative + fail-open; completes R3 C1's sector-cap dimension).

See docs/superpowers/specs/2026-07-04-run163943-exit-quality-and-c1-fix-design.md
"""
import os
import sys

_BACKEND = os.path.join(os.path.dirname(__file__), "..")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

import pytest  # noqa: E402
import strategies.graph_nexus_analysis as gna  # noqa: E402


def _ph(sym, closes):
    return {sym: [{"close": c} for c in closes]}


# ---------------------------------------------------------------------------
# Track C — _recent_runup_protect
# ---------------------------------------------------------------------------
class TestRecentRunupProtect:
    def test_disabled_when_block_pct_zero(self):
        assert gna._recent_runup_protect("X", _ph("X", [10, 20, 15]), 0.0, 20) == (False, 0.0)

    def test_protects_when_runup_exceeds_threshold(self):
        # range 10->20 = +100% > 25% threshold
        protected, pct = gna._recent_runup_protect("X", _ph("X", [10, 20, 16]), 25.0, 20)
        assert protected is True
        assert pct == pytest.approx(100.0)

    def test_not_protected_when_runup_below_threshold(self):
        # range 100->110 = +10% < 25%
        protected, pct = gna._recent_runup_protect("X", _ph("X", [100, 110, 105]), 25.0, 20)
        assert protected is False
        assert pct == pytest.approx(10.0)

    def test_fail_open_on_missing_history(self):
        assert gna._recent_runup_protect("X", None, 25.0, 20) == (False, 0.0)
        assert gna._recent_runup_protect("X", {}, 25.0, 20) == (False, 0.0)
        assert gna._recent_runup_protect("X", _ph("X", [10]), 25.0, 20) == (False, 0.0)

    def test_respects_lookback_window(self):
        # old +100% runup falls outside a 2-bar lookback; recent 2 bars flat
        protected, pct = gna._recent_runup_protect("X", _ph("X", [10, 20, 30, 30]), 25.0, 2)
        assert protected is False


# ---------------------------------------------------------------------------
# Track C — V28.9 losing break-glass guard config semantics (default off)
# ---------------------------------------------------------------------------
class TestBreakGlassRunupGuardConfig:
    def test_default_off_means_helper_returns_not_protected(self):
        # With the default block_pct=0, a huge run-up is NOT protected — so the
        # break-glass path behaves exactly as before this change.
        protected, _ = gna._recent_runup_protect(
            "UAL", {"UAL": [{"close": 90}, {"close": 120}]}, 0.0, 20)
        assert protected is False

    def test_enabled_protects_recent_runup_name(self):
        protected, pct = gna._recent_runup_protect(
            "UAL", {"UAL": [{"close": 90}, {"close": 120}, {"close": 105}]}, 25.0, 20)
        assert protected is True  # +33% range > 25%


# ---------------------------------------------------------------------------
# Track A — _rotation_incoming_sector_cap_ok
# ---------------------------------------------------------------------------
class TestRotationIncomingSectorCapOk:
    def _emu(self, positions):
        class _E:
            _positions: dict = {}
        e = _E()
        e._positions = dict(positions)
        return e

    def setup_method(self):
        self._saved = gna._neo4j_stock_sector_cache
        gna._neo4j_stock_sector_cache = {
            "tech": ["AMD", "SMCI", "LRCX", "NVDA"],
            "travel": ["VIK", "UAL"],
        }

    def teardown_method(self):
        gna._neo4j_stock_sector_cache = self._saved

    def _cfg(self, **ov):
        c = {
            "max_sector_portfolio_enabled": True,
            "max_sector_portfolio_pct": 0.40,
            "rotation_prevalidate_sector_cap_enabled": True,
        }
        c.update(ov)
        return c

    def test_blocks_buy_into_already_over_cap_sector(self):
        # tech already $45k of $100k (>40% cap); buying more tech (AMD) blocked
        emu = self._emu({"NVDA": 100})  # 100 * 450 = 45k
        ok, reason = gna._rotation_incoming_sector_cap_ok(
            "AMD", 5000, emu, 100000, self._cfg(),
            prices={"NVDA": 450.0, "AMD": 150.0}, selling_sym="VIK")
        assert ok is False
        assert "cap" in reason

    def test_allows_buy_when_sector_under_cap(self):
        emu = self._emu({"NVDA": 20})  # 20*450 = 9k tech
        ok, _ = gna._rotation_incoming_sector_cap_ok(
            "AMD", 5000, emu, 100000, self._cfg(),
            prices={"NVDA": 450.0, "AMD": 150.0}, selling_sym="VIK")
        assert ok is True  # 9k + 5k = 14k < 40k

    def test_same_sector_sell_is_excluded_from_exposure(self):
        # tech = NVDA 45k, but we are SELLING NVDA (same sector) to buy AMD:
        # exposure after removing NVDA = 0, +5k buy < cap -> allowed
        emu = self._emu({"NVDA": 100})
        ok, _ = gna._rotation_incoming_sector_cap_ok(
            "AMD", 5000, emu, 100000, self._cfg(),
            prices={"NVDA": 450.0, "AMD": 150.0}, selling_sym="NVDA")
        assert ok is True

    def test_fail_open_when_flag_disabled(self):
        emu = self._emu({"NVDA": 100})
        ok, _ = gna._rotation_incoming_sector_cap_ok(
            "AMD", 5000, emu, 100000,
            self._cfg(rotation_prevalidate_sector_cap_enabled=False),
            prices={"NVDA": 450.0, "AMD": 150.0})
        assert ok is True

    def test_fail_open_when_no_sector_cache(self):
        gna._neo4j_stock_sector_cache = {}
        emu = self._emu({"NVDA": 100})
        ok, _ = gna._rotation_incoming_sector_cap_ok(
            "AMD", 5000, emu, 100000, self._cfg(), prices={"NVDA": 450.0})
        assert ok is True
