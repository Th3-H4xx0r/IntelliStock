"""The index core is not portfolio-swap fodder (bt 823150).

`_mw_open_set` carries the broker sleeve's own legs, so SPY entered the
weakest-candidate pool for the V31.1 portfolio swap. Being a low-beta index
against a rising satellite it sorts weakest almost every bar, so the swap kept
proposing to sell the ENTIRE core to buy one name:

    V31.7 portfolio_swap weakest candidates: SPY(pnl=+1.8%,d=11,...), AMD(...)
    ROTATION PREVALIDATE sector-cap: skip incoming SNDK
        (sector 'technology' $4,601 > 40% cap $2,481) - swap skipped, keeping SPY

$4,601 = ON $444 + AMD $94 + a $4,063 buy - the core position itself. Verified
against Neo4j that SPY has no Company node, so it classifies 'unknown'; the buy
SIZE breached the cap, not SPY's sector. SNDK was never bought.

Both outcomes are wrong: blocked, the winner is refused; fired, the core is
liquidated wholesale into one name straight through `core_min_pct`.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "strategies"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from strategies.graph_nexus_analysis import _sleeve_symbols  # noqa: E402


ON_CFG = {
    "residual_sleeve_enabled": True,
    "residual_sleeve_symbol": "SPY",
    "residual_sleeve_bear_symbol": "SQQQ",
    "momentum_swap_exclude_sleeve_legs": True,
}


def _pool(open_set, config):
    """Mirror of the broker-side candidate filter in the portfolio-swap lane."""
    sleeve = (
        _sleeve_symbols(config)
        if bool(config.get("momentum_swap_exclude_sleeve_legs", False))
        else set()
    )
    return [s for s in open_set if str(s).strip().upper() not in sleeve]


def test_core_leg_is_excluded_from_the_swap_pool():
    """The exact 823150 book on the bar SNDK ranked #1."""
    assert _pool(["SPY", "AMD", "OXY", "CPER", "GDX", "ON"], ON_CFG) == [
        "AMD", "OXY", "CPER", "GDX", "ON"]


def test_bear_leg_is_excluded_too():
    """Selling the hedge to chase a long is the same failure in a bear."""
    assert "SQQQ" not in _pool(["SQQQ", "AMD"], ON_CFG)


def test_default_off_is_byte_identical():
    """Every existing document must be unaffected until it opts in."""
    cfg = dict(ON_CFG)
    cfg["momentum_swap_exclude_sleeve_legs"] = False
    assert _pool(["SPY", "AMD"], cfg) == ["SPY", "AMD"]


def test_inert_when_the_sleeve_is_disabled():
    cfg = dict(ON_CFG)
    cfg["residual_sleeve_enabled"] = False
    assert _pool(["SPY", "AMD"], cfg) == ["SPY", "AMD"]


def test_padded_config_value_still_matches_a_position_key():
    """The bug _sleeve_symbols' own docstring warns about: a padded value must
    not produce an exemption set no position key ever matches."""
    cfg = dict(ON_CFG)
    cfg["residual_sleeve_symbol"] = " SPY "
    assert _pool(["SPY", "AMD"], cfg) == ["AMD"]


def test_an_alpha_position_is_never_exempted():
    """The exemption must not become a way for real holdings to dodge rotation."""
    assert _pool(["AMD", "SNDK", "ON"], ON_CFG) == ["AMD", "SNDK", "ON"]


def test_pool_is_not_emptied_when_the_core_is_the_only_holding():
    """Degenerate book: excluding the core leaves nothing to swap, which must
    simply mean 'no swap', never a crash or a swap of the core by fallback."""
    assert _pool(["SPY"], ON_CFG) == []


def test_swap_size_after_exclusion_is_a_position_not_the_whole_core():
    """Why this also fixes sizing.

    With SPY in the pool the swap freed the whole $4,063 core. With it out, the
    pool falls back to a real alpha position and
    `momentum_position_size_floor_pct` tops the buy up to ~10% of NAV — the
    objective's target, and comfortably under the 40% sector cap.
    """
    nav = 6203.0
    held = {"SPY": 4063.0, "AMD": 94.0, "ON": 444.0, "OXY": 444.0}
    floor_pct = 0.10

    pool = _pool(list(held), ON_CFG)
    freed = min(held[s] for s in pool)
    alloc = max(freed, nav * floor_pct)

    assert alloc == pytest.approx(620.3)
    assert alloc / nav == pytest.approx(0.10)
    # sector cap: ON stays, AMD is the sold leg
    assert held["ON"] + alloc < 0.40 * nav
    # the pre-fix behaviour breached it
    assert held["ON"] + held["AMD"] + held["SPY"] > 0.40 * nav
