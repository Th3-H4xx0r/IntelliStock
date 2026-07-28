"""momentum_breakout_add must honour the gates its sibling lanes apply (2026-07-25).

FORENSICS (bt#500437, window 2026-03-02..04-27): this lane fired 5 times for
$3,512.96 and produced ZERO winners -- -$376.97, which is 45% of every loss in
the run. Counting bt#211684 it is 7 fills, 7 losers.

Every one of those fills happened on a bar where a sibling lane had ALREADY
REFUSED the same ticker, e.g.:

    V32 mw_buy extension-block: ALM recent runup +82.4% > 25% - no conviction bypass
      -> Momentum breakout add: buy ALM (score=1.266, $720 from free cash)

    V32 P3 B-1 mw_buy blacklist-block: FTH (5 bars remaining)
      -> Momentum breakout add: buy FTH ($702 from free cash)

It also bypassed the position cap -- `Buy gate inputs for FTH: ... open_pos=11
... -> PASS` against a regime cap of 8 -- because it counted against the raw
`max_positions` while every other lane used the regime cap. And it self-sized at
`momentum_breakout_position_pct` (0.12) of NAV, producing the largest position in
the bear book (ALM $720 = 12.0% of NAV vs the +6.88% reference's largest at 6.9%).

`momentum_breakout_add_respect_gates` (default OFF) makes the lane consult the
same blacklist + extension gate as mw_buy/mw_swap and use the regime cap;
`momentum_breakout_max_nav_pct` (default 0 = off) adds a hard dollar ceiling.
A count-based limit cannot fix the sizing -- it trims the tail of the size
distribution, never the head; only a dollar limiter binds.
"""
import os
import sys

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

import strategies.graph_nexus_analysis as g

_CFG_EXT = {"entry_extension_block_pct": 25.0, "portfolio_swap_ath_gate_enabled": False}

# ALM's actual 2026-03-02 shape: +82.4% runup over the 20-bar lookback.
_ALM = [{"close": 11.2}] * 5 + [{"close": 20.4}] * 15
# A calm name that should still be allowed through.
_CALM = [{"close": 185.0}] * 10 + [{"close": 190.0}] * 10


def test_extension_gate_fires_for_the_breakout_add_lane():
    """The lane name must not be a loophole -- same verdict as its siblings."""
    for lane in ("mw_buy", "mw_swap", "mw_breakout_add"):
        blocked, why = g._v32_momentum_ath_or_mcap_block(
            "ALM", 20.4, {"ALM": _ALM}, {}, _CFG_EXT, 1.266, lane=lane)
        assert blocked, lane
        assert "extension" in why, (lane, why)


def test_extension_gate_still_allows_calm_names():
    blocked, _ = g._v32_momentum_ath_or_mcap_block(
        "CVX", 190.0, {"CVX": _CALM}, {}, _CFG_EXT, 1.0, lane="mw_breakout_add")
    assert not blocked


def test_high_conviction_cannot_bypass_the_extension_gate():
    """ALM was admitted at score 1.266 and FTH on a blacklist -- a high score
    must not buy its way past an 82% runup."""
    for score in (1.266, 2.5, 5.56):
        blocked, _ = g._v32_momentum_ath_or_mcap_block(
            "ALM", 20.4, {"ALM": _ALM}, {}, _CFG_EXT, score, lane="mw_breakout_add")
        assert blocked, score


# ---------------------------------------------------------------- regime cap
_CAP_CFG = {"max_positions": 14, "max_positions_bull": 14, "max_positions_chop": 8,
            "max_positions_bear": 2, "max_positions_crash": 0}


def _effective_cap(regime, recovery=False, cfg=None):
    """Mirror of the lane's new cap computation."""
    c = cfg or _CAP_CFG
    return min(int(c.get("max_positions", 15)),
               g._apply_recovery_cap(g._regime_position_cap(c, regime), recovery, c))


def test_lane_cap_follows_the_regime_not_raw_max_positions():
    """`open_pos=11 -> PASS` against a cap of 8 is the defect being closed."""
    assert _effective_cap("bear") == 2
    assert _effective_cap("chop") == 8
    assert _effective_cap("crash") == 0
    assert _effective_cap("bull") == 14


def test_lane_cap_honours_a_confirmed_recovery():
    cfg = dict(_CAP_CFG, max_positions_recovery=14)
    assert _effective_cap("chop", recovery=True, cfg=cfg) == 14
    assert _effective_cap("bear", recovery=True, cfg=cfg) == 14


# ---------------------------------------------------------------- NAV ceiling
def _alloc(nav, free_cash, pos_pct=0.12, nav_cap=0.0, min_pos=100.0):
    """Mirror of the lane's sizing, including the new ceiling."""
    target = nav * pos_pct if nav > 0 else 0.0
    alloc = min(free_cash * 0.95, max(min_pos, target))
    if nav_cap > 0 and nav > 0:
        alloc = min(alloc, nav * nav_cap)
    return alloc


def test_nav_ceiling_off_by_default_reproduces_the_bad_size():
    """$6000 NAV at 12% is the $720 ALM position that lost $80.51."""
    assert _alloc(6000.0, 5000.0) == 720.0


def test_nav_ceiling_binds_when_set():
    """6% matches the +6.88% reference's largest position (6.9% of NAV)."""
    assert _alloc(6000.0, 5000.0, nav_cap=0.06) == 360.0
    assert _alloc(6000.0, 5000.0, nav_cap=0.05) == 300.0


def test_nav_ceiling_never_raises_an_allocation():
    """A generous ceiling must not inflate a cash-constrained buy."""
    assert _alloc(6000.0, 300.0, nav_cap=0.50) == 285.0   # 0.95 * free cash


# ------------------------------------------------- A2: current-regime NAV cap
# The lane read the scalar `momentum_breakout_max_nav_pct` AFTER the broker had
# already overlaid the PREVIOUS cycle's regime profile, so a confirmed-bull
# breakout stayed at the base ceiling while a downgraded cycle could still
# spend the bull one. The mapping below is base-only and read against THIS
# cycle's regime.
_NAV_MAP = {"default": 0.06, "bull": 0.10, "recovery": 0.08}


def _nav_cfg(**kw):
    c = {"momentum_breakout_max_nav_pct": 0.06,
         "momentum_breakout_add_respect_gates": True,
         "momentum_breakout_max_nav_pct_by_regime": dict(_NAV_MAP)}
    c.update(kw)
    return c


def _cache(regime, recovery=False):
    return {"_market_regime": regime, "_market_regime_recovery": recovery}


def test_nav_cap_legacy_scalar_when_mapping_absent():
    """Byte-compatible: no mapping => the existing scalar, whatever the regime."""
    cfg = _nav_cfg()
    cfg.pop("momentum_breakout_max_nav_pct_by_regime")
    for regime in ("bull", "chop", "bear", "crash", ""):
        assert g._resolve_momentum_breakout_nav_cap(
            cfg, _cache(regime, True)) == 0.06, regime
    # And the lane's own default (0 = off) survives.
    assert g._resolve_momentum_breakout_nav_cap({}, _cache("bull")) == 0.0


def test_nav_cap_bull_uses_bull_entry():
    assert g._resolve_momentum_breakout_nav_cap(
        _nav_cfg(), _cache("bull")) == 0.10


def test_nav_cap_bull_requires_gate_coherence():
    """The raised ceiling is only safe when the lane honours its sibling gates."""
    assert g._resolve_momentum_breakout_nav_cap(
        _nav_cfg(momentum_breakout_add_respect_gates=False),
        _cache("bull")) == 0.06


def test_nav_cap_recovery_is_chop_plus_flag():
    assert g._resolve_momentum_breakout_nav_cap(
        _nav_cfg(), _cache("chop", True)) == 0.08
    assert g._resolve_momentum_breakout_nav_cap(
        _nav_cfg(), _cache("chop", False)) == 0.06


def test_nav_cap_non_bull_labels_use_default_not_previous_scalar():
    for regime in ("chop", "bear", "crash", "", "unknown", None):
        assert g._resolve_momentum_breakout_nav_cap(
            _nav_cfg(), _cache(regime)) == 0.06, regime


def test_nav_cap_requires_all_three_keys():
    for missing in ("default", "bull", "recovery"):
        m = dict(_NAV_MAP)
        m.pop(missing)
        assert g._resolve_momentum_breakout_nav_cap(
            _nav_cfg(momentum_breakout_max_nav_pct_by_regime=m),
            _cache("bull")) == 0.06, missing


def test_nav_cap_rejects_invalid_entries():
    for bad in (0, -0.1, 1.5, float("nan"), float("inf"), "x", True, None, []):
        m = dict(_NAV_MAP, bull=bad)
        assert g._resolve_momentum_breakout_nav_cap(
            _nav_cfg(momentum_breakout_max_nav_pct_by_regime=m),
            _cache("bull")) == 0.06, bad
    for bad in (None, [], "x", 0.06):
        assert g._resolve_momentum_breakout_nav_cap(
            _nav_cfg(momentum_breakout_max_nav_pct_by_regime=bad),
            _cache("bull")) == 0.06, bad


def test_nav_cap_accepts_full_nav_and_missing_cache():
    m = dict(_NAV_MAP, bull=1.0)
    assert g._resolve_momentum_breakout_nav_cap(
        _nav_cfg(momentum_breakout_max_nav_pct_by_regime=m),
        _cache("bull")) == 1.0
    assert g._resolve_momentum_breakout_nav_cap(_nav_cfg(), None) == 0.06
