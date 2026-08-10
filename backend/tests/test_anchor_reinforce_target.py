"""2026-08-10: the anchor-reinforcement target must exceed the entry clip.

bt 571147 (+17.36%, the best run on record) logged

    V31 anchor reinforcement budget: cap=$178 (40% of stock_budget=$444), candidates=6

on 25 separate bars — 4 to 6 qualified winners every time — and funded ZERO adds.
Nothing in the log said so, because until now only the BUDGET was logged.

The cause is arithmetic, and it is the fourth instance of one pattern in this repo
(conviction overflow band $600 < clip $840; BFQ pool $155-$385 < clip $840; and this):

    anchor_reinforce_target_pct 12  ->  target 0.12 x NAV = $720
    total_spend_cap_target_weight_pct 0.14  ->  entry   0.14 x NAV = $840

A position entered at the clip is ALREADY worth more than its reinforcement target
before it has gained a cent, so `additional_needed = max(0, target - current_value)`
is 0 at every stage, for every winner, forever.

These tests drive the real `_plan_anchor_reinforcement`.
"""
import os
import sys

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

import strategies.graph_nexus_analysis as g  # noqa: E402

NAV = 6000.0
ENTRY = 840.0  # 0.14 x NAV, the live total_spend_cap_target_weight_pct


def _cand(pnl_pct, held_days, *, raw=1.7, drop=0.0, ticker="WIN"):
    return {
        "ticker": ticker,
        "is_equity": True,
        "held_days": held_days,
        "unrealized_pct": pnl_pct,
        "drop_from_peak_pct": drop,
        "entry_notional": ENTRY,
        "raw_net_score": raw,
    }


def _cfg(**kw):
    base = {
        "anchor_reinforce_enabled": True,
        "anchor_reinforce_stage1_days": 7, "anchor_reinforce_stage1_pnl": 15, "anchor_reinforce_stage1_mult": 1.3,
        "anchor_reinforce_stage2_days": 14, "anchor_reinforce_stage2_pnl": 30, "anchor_reinforce_stage2_mult": 1.6,
        "anchor_reinforce_stage3_days": 21, "anchor_reinforce_stage3_pnl": 50, "anchor_reinforce_stage3_mult": 2.0,
        "anchor_reinforce_max_drawdown_from_peak_pct": 5.0,
        "nexus_high_conviction_threshold": 1.5,
        "min_position_size": 100,
    }
    base.update(kw)
    return base


def test_the_live_setting_funds_nothing_at_any_stage():
    """anchor_reinforce_target_pct=12 against a 14% entry clip: zero, three times.
    This is the bug, reproduced from the real function."""
    cfg = _cfg(anchor_reinforce_target_pct=12)
    for pnl, days in ((15, 8), (30, 15), (50, 22)):
        funded, remaining = g._plan_anchor_reinforcement(
            [_cand(pnl, days)], 5000.0,
            min_position_size=100, config=cfg, strategy_cache={}, portfolio_total=NAV)
        assert funded == [], (
            f"expected the documented zero-add bug at +{pnl}%/{days}d, got {funded}")
        assert remaining == 5000.0


def test_a_target_above_the_entry_clip_funds_every_stage():
    cfg = _cfg(anchor_reinforce_target_pct=20)
    expected = {1: 234.0, 2: 385.0, 3: 586.0}
    for stage, (pnl, days) in enumerate(((15, 8), (30, 15), (50, 22)), start=1):
        funded, _ = g._plan_anchor_reinforcement(
            [_cand(pnl, days)], 5000.0,
            min_position_size=100, config=cfg, strategy_cache={}, portfolio_total=NAV)
        assert len(funded) == 1, f"stage {stage} funded nothing"
        assert funded[0]["anchor_stage"] == stage
        assert abs(funded[0]["buy_cash"] - expected[stage]) < 1.0, (
            f"stage {stage}: expected ~${expected[stage]}, got ${funded[0]['buy_cash']}")


def test_the_boundary_is_the_entry_clip_not_a_tuned_number():
    """The lane switches on where the target clears the position's value at the
    stage-1 trigger (0.14 x 1.15 = 16.1% of NAV) PLUS min_position_size ($100 =
    1.67% of NAV) — i.e. ~17.8%. Not fitted to 20; 20 just carries margin.

    The real function taught this: at target_pct=17 the gap is only $54 and
    `additional_needed < min_position_size` refuses it. An arithmetic sketch on
    paper said 17 would fund.
    """
    for pct, should_fund in ((16, False), (17, False), (18, True), (20, True), (25, True)):
        cfg = _cfg(anchor_reinforce_target_pct=pct)
        funded, _ = g._plan_anchor_reinforcement(
            [_cand(15, 8)], 5000.0,
            min_position_size=100, config=cfg, strategy_cache={}, portfolio_total=NAV)
        assert bool(funded) is should_fund, (
            f"target_pct={pct}: expected funded={should_fund}, got {funded}")


def test_a_winner_in_a_deep_pullback_is_still_refused():
    """Raising the target must not disarm the averaging-up guard. The 8 big winners
    drew down 6.9-22.4% on the way up, so this gate is what stops an add at a top."""
    cfg = _cfg(anchor_reinforce_target_pct=20, anchor_reinforce_hc_max_drawdown_pct=10.0)
    funded, _ = g._plan_anchor_reinforcement(
        [_cand(30, 15, drop=18.0)], 5000.0,
        min_position_size=100, config=cfg, strategy_cache={}, portfolio_total=NAV)
    assert funded == [], "an 18% drop from peak must not be averaged into"


def test_each_stage_fires_at_most_once_per_position():
    cfg = _cfg(anchor_reinforce_target_pct=20)
    cache = {}
    first, _ = g._plan_anchor_reinforcement(
        [_cand(15, 8)], 5000.0,
        min_position_size=100, config=cfg, strategy_cache=cache, portfolio_total=NAV)
    assert len(first) == 1
    second, _ = g._plan_anchor_reinforcement(
        [_cand(15, 8)], 5000.0,
        min_position_size=100, config=cfg, strategy_cache=cache, portfolio_total=NAV)
    assert second == [], "stage 1 must be idempotent within a run"


def test_the_budget_still_binds():
    """A $150 budget cannot fund a $234 stage-1 add — the lane must not overspend."""
    cfg = _cfg(anchor_reinforce_target_pct=20)
    funded, remaining = g._plan_anchor_reinforcement(
        [_cand(15, 8)], 150.0,
        min_position_size=100, config=cfg, strategy_cache={}, portfolio_total=NAV)
    assert len(funded) == 1 and funded[0]["buy_cash"] <= 150.0
    assert remaining >= 0.0
