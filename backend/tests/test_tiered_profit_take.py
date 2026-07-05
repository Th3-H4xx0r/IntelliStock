"""Tests for tiered partial profit-take (2026-07-05).

Staged scale-out: each [gain%, sell_fraction] tier fires once, lowest crossed
tier first, one per bar. Default-preserving: no profit_take_tiers => existing
single-fire behaviour. See
docs/superpowers/specs/2026-07-05-tiered-profit-take-design.md
"""
import os
import sys

_BACKEND = os.path.join(os.path.dirname(__file__), "..")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

import json  # noqa: E402
import re  # noqa: E402
from datetime import datetime, timezone  # noqa: E402

import pytest  # noqa: E402
import strategies.graph_nexus_analysis as gna  # noqa: E402

TIERS = {"profit_take_tiers": [[20, 0.33], [40, 0.5]]}


# ---------------------------------------------------------------------------
# Pure helper
# ---------------------------------------------------------------------------
class TestProfitTakeNextTier:
    def test_no_tiers_returns_none(self):
        assert gna._profit_take_next_tier({}, 50.0, "e1", None) is None
        assert gna._profit_take_next_tier({"profit_take_tiers": []}, 50.0, "e1", None) is None

    def test_fires_lowest_crossed_tier_first(self):
        r = gna._profit_take_next_tier(TIERS, 25.0, "e1", None)
        assert r["gain"] == 20 and r["fraction"] == 0.33
        assert r["new_marker"] == {"entry": "e1", "fired": [20.0]}

    def test_below_first_tier_returns_none(self):
        assert gna._profit_take_next_tier(TIERS, 15.0, "e1", None) is None

    def test_one_tier_per_bar_even_when_both_crossed(self):
        prior = {"entry": "e1", "fired": [20.0]}
        r = gna._profit_take_next_tier(TIERS, 45.0, "e1", prior)
        assert r["gain"] == 40 and r["fraction"] == 0.5
        assert r["new_marker"]["fired"] == [20.0, 40.0]

    def test_all_tiers_fired_returns_none(self):
        prior = {"entry": "e1", "fired": [20.0, 40.0]}
        assert gna._profit_take_next_tier(TIERS, 60.0, "e1", prior) is None

    def test_reentry_resets_fired(self):
        prior = {"entry": "OLD", "fired": [20.0, 40.0]}
        r = gna._profit_take_next_tier(TIERS, 25.0, "NEW", prior)
        assert r["gain"] == 20 and r["new_marker"]["fired"] == [20.0]

    def test_legacy_string_marker_treats_tiers_at_or_below_gain_pct_as_fired(self):
        # legacy single-fire fired at profit_take_gain_pct; every tier <= it is taken
        cfg = {"profit_take_tiers": [[20, 0.33], [40, 0.5]], "profit_take_gain_pct": 40}
        assert gna._profit_take_next_tier(cfg, 45.0, "e1", "e1") is None  # both tiers <= 40
        cfg2 = {"profit_take_tiers": [[20, 0.33], [40, 0.5]], "profit_take_gain_pct": 25}
        r = gna._profit_take_next_tier(cfg2, 45.0, "e1", "e1")
        assert r["gain"] == 40  # only tier 20 (<=25) taken; tier 40 still armed

    def test_malformed_tiers_skipped(self):
        cfg = {"profit_take_tiers": [["x", "y"], [20, 1.5], [25, 0.4]]}
        r = gna._profit_take_next_tier(cfg, 30.0, "e1", None)
        assert r["gain"] == 25 and r["fraction"] == 0.4


# ---------------------------------------------------------------------------
# Integration through _evaluate_position_risk
# ---------------------------------------------------------------------------
class _Emu:
    def __init__(self):
        self._positions = {}
        self._trades = []

    def add(self, t, sh, px, ts=None):
        self._positions[t] = self._positions.get(t, 0.0) + sh
        self._trades.append({
            "ticker": t, "action": "buy", "price": float(px), "shares": float(sh),
            "timestamp": ts or datetime(2026, 1, 1, tzinfo=timezone.utc),
        })


def _cfg(**ov):
    # isolate profit-take: no other exit should fire at a modest positive gain
    c = {"profit_take_enabled": True, "profit_take_tiers": [[20, 0.33], [40, 0.5]],
         "max_open_loss_pct": -50.0, "fast_loser_cut_pct": -50.0,
         "trailing_stop_activation_pct": 90.0, "trailing_stop_pct": 90.0,
         "max_hold_days": 999, "profit_take_gain_pct": 40.0, "profit_take_sell_fraction": 0.5}
    c.update(ov)
    return c


def _eval(sym, gain_pct, cache, cfg, held_days=100):
    emu = _Emu(); emu.add(sym, 100, 100.0)
    return gna._evaluate_position_risk(
        sym, fresh_score=0, fresh_reason="No graph signal", config=cfg,
        portfolio_emulator=emu, strategy_cache=cache,
        prices={sym: 100.0 * (1 + gain_pct / 100.0)},
        price_history={}, date_key="2026-04-01", propagated={},
        entry_buy_ts=datetime(2026, 1, 1, tzinfo=timezone.utc),
        held_days=held_days, max_hold_days=999)


class TestTierIntegration:
    def test_tier1_fires_with_tier_fraction(self):
        cache = {}
        score, reason, extras = _eval("FOO", 25.0, cache, _cfg())
        assert score == -1 and "Profit take" in reason
        assert extras["sell_fraction"] == 0.33
        assert cache["_nexus_profit_take_state"]["FOO"]["fired"] == [20.0]

    def test_tier2_fires_after_tier1_with_its_fraction(self):
        cache = {"_nexus_profit_take_state": {"FOO": {"entry": "2026-01-01T00:00:00+00:00", "fired": [20.0]}}}
        score, reason, extras = _eval("FOO", 45.0, cache, _cfg())
        assert score == -1 and extras["sell_fraction"] == 0.5

    def test_no_refire_same_tier(self):
        cache = {"_nexus_profit_take_state": {"FOO": {"entry": "2026-01-01T00:00:00+00:00", "fired": [20.0]}}}
        score, reason, _ = _eval("FOO", 25.0, cache, _cfg())
        assert score != -1 or "Profit take" not in (reason or "")

    def test_tiers_absent_uses_single_fire(self):
        cache = {}
        cfg = _cfg(profit_take_tiers=[], profit_take_gain_pct=20.0, profit_take_sell_fraction=0.5)
        score, reason, extras = _eval("FOO", 25.0, cache, cfg)
        assert score == -1 and extras["sell_fraction"] == 0.5

    def test_dict_marker_config_flip_does_not_refire_single_fire(self):
        # #3: config flipped tiers->single-fire; a dict marker from a prior tiered
        # fire must be recognized as "already taken" (not re-fire an extra sell).
        ek = "2026-01-01T00:00:00+00:00"
        cache = {"_nexus_profit_take_state": {"FOO": {"entry": ek, "fired": [40.0]}}}
        cfg = _cfg(profit_take_tiers=[], profit_take_gain_pct=40.0, profit_take_sell_fraction=0.5)
        score, reason, _ = _eval("FOO", 45.0, cache, cfg)
        assert score != -1 or "Profit take" not in (reason or "")

    def test_invalid_tiers_falls_back_to_single_fire(self):
        # #7: non-empty but structurally-invalid tiers -> single-fire, not disabled
        cache = {}
        cfg = _cfg(profit_take_tiers=[["x", "y"], [20]], profit_take_gain_pct=20.0, profit_take_sell_fraction=0.5)
        score, reason, extras = _eval("FOO", 25.0, cache, cfg)
        assert score == -1 and "Profit take" in reason and extras["sell_fraction"] == 0.5

    def test_no_tier_fire_without_entry_key(self):
        # #6: no entry timestamp -> no tiered fire (avoids empty-key marker collisions)
        emu = _Emu(); emu.add("FOO", 100, 100.0)
        score, reason, _ = gna._evaluate_position_risk(
            "FOO", fresh_score=0, fresh_reason="No graph signal", config=_cfg(),
            portfolio_emulator=emu, strategy_cache={}, prices={"FOO": 125.0},
            price_history={}, date_key="2026-04-01", propagated={},
            entry_buy_ts=None, held_days=100, max_hold_days=999)
        assert score != -1 or "Profit take" not in (reason or "")


class TestSchema:
    def test_new_keys_registered(self):
        line1 = open(gna.__file__, encoding="utf-8").readline()
        cfg = json.loads(re.search(r"INTELLISTOCK_SCHEMA:\s*(\{.*\})\s*$", line1.strip()).group(1))["config"]
        assert cfg["profit_take_enabled"] is False
        assert cfg["profit_take_tiers"] == []
