"""Tests for the index-core sizing prototype (backend/core_sleeve.py).

The module is not wired to anything; these tests pin the arithmetic and, most
importantly, the DEFAULT-OFF contract — an untouched doc-179 config must parse
to a disabled core and produce zero orders.

Design: docs/strategies/index-core-allocation-design.md
"""
import os
import sys

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from core_sleeve import (  # noqa: E402
    MIN_CORE_DEPLOY_USD,
    core_rebalance_order,
    core_sleeve_config,
    core_target_weight,
    turnover_budget_state,
)

# Trimmed from the live Strategies doc 179 (read-only, 2026-08-03). None of the
# core_* keys exist there, which is the point of the first test.
DOC179_SUBSET = {
    "nexus_portfolio_pct": 0.95,
    "etf_portfolio_pct": 0.15,
    "cash_reserve_floor_pct": 0.02,
    "single_position_max_pct": 25,
    "residual_sleeve_enabled": True,
    "residual_sleeve_symbol": "SPY",
    "residual_sleeve_bear_symbol": "",
    "residual_sleeve_release_cash_pct": 0.15,
    "max_positions": 14,
}

ON = dict(DOC179_SUBSET, core_sleeve_enabled=True)


# ── default-off contract ──────────────────────────────────────────────────

def test_live_config_parses_to_disabled_core():
    cfg = core_sleeve_config(DOC179_SUBSET)
    assert cfg.enabled is False
    assert cfg.cash_floor_pct == 0.02  # reuses the EXISTING key, not a new one


def test_disabled_core_emits_no_order_in_any_state():
    cfg = core_sleeve_config(DOC179_SUBSET)
    for regime in ("", "bull", "chop", "bear", "crash"):
        order = core_rebalance_order(
            cfg, nav=6000.0, core_value=0.0, satellite_value=4000.0,
            cash=2000.0, regime=regime, bear_dwell_days=99,
            funding_request=5000.0)
        assert order.notional == 0.0
        assert order.is_action is False
        assert order.reason == "disabled"
    assert core_target_weight(cfg, satellite_weight=0.0) == 0.0
    assert turnover_budget_state(cfg, rolling_notional=1e9, nav=6000.0) \
        == (False, 0.0)


def test_empty_and_garbage_config_never_raise():
    for bad in (None, {}, {"core_target_pct": "", "core_min_pct": None},
                {"core_sleeve_enabled": True, "core_target_pct": "abc",
                 "core_rebalance_min_days": "x"}):
        cfg = core_sleeve_config(bad)
        core_rebalance_order(cfg, nav=6000.0, core_value=0.0,
                             satellite_value=0.0, cash=6000.0)


def test_non_finite_target_falls_back_to_default():
    cfg = core_sleeve_config({"core_sleeve_enabled": True,
                              "core_target_pct": float("nan")})
    assert cfg.target_pct == 0.60


# ── the tilt rule ─────────────────────────────────────────────────────────

def test_no_opinion_holds_the_core_not_cash():
    """The whole point of the restructure: an empty satellite means fully
    invested in the index, not 25% cash (the measured mean on bt 987397)."""
    cfg = core_sleeve_config(ON)
    assert core_target_weight(cfg, satellite_weight=0.0) == 0.98


def test_buy_becomes_an_overweight_funded_by_the_index():
    cfg = core_sleeve_config(ON)
    before = core_target_weight(cfg, satellite_weight=0.20)
    after = core_target_weight(cfg, satellite_weight=0.38)
    assert after < before
    # Total long exposure is unchanged: the graph moved weight out of the index
    # into a name, which is exactly what an active overweight is.
    assert round(before + 0.20, 10) == round(after + 0.38, 10) == 0.98


def test_sell_returns_weight_to_the_index_not_to_cash():
    cfg = core_sleeve_config(ON)
    assert core_target_weight(cfg, satellite_weight=0.38) == 0.60
    assert core_target_weight(cfg, satellite_weight=0.10) == 0.88


def test_core_floor_bounds_how_much_the_graph_can_command():
    cfg = core_sleeve_config(ON)
    assert core_target_weight(cfg, satellite_weight=0.95) == 0.30


def test_bear_derisk_halves_the_core_and_needs_persistence():
    cfg = core_sleeve_config(ON)
    # Day 1 of a "bear" does nothing — the failure mode broker.py:2720 documents.
    assert core_target_weight(cfg, satellite_weight=0.38, regime="bear",
                              bear_dwell_days=1) == 0.60
    assert core_target_weight(cfg, satellite_weight=0.38, regime="bear",
                              bear_dwell_days=3) == 0.30
    assert core_target_weight(cfg, satellite_weight=0.38, regime="crash",
                              bear_dwell_days=5) == 0.30


def test_bear_derisk_is_bounded_not_a_liquidation():
    """Today's `_residual_sleeve_release` sets frac=1.0 on bear/crash. That is
    the largest single turnover event the system can produce."""
    cfg = core_sleeve_config(ON)
    assert core_target_weight(cfg, satellite_weight=0.0, regime="bear",
                              bear_dwell_days=9) > 0.0


# ── the rebalance rule (the turnover fix) ─────────────────────────────────

def test_inside_the_band_does_nothing():
    cfg = core_sleeve_config(ON)
    order = core_rebalance_order(cfg, nav=6000.0, core_value=3720.0,
                                 satellite_value=2280.0, cash=0.0,
                                 days_since_rebalance=99)
    assert order.reason == "within_band"
    assert order.notional == 0.0


def test_band_breach_deploys_once_the_cadence_allows():
    cfg = core_sleeve_config(ON)
    kw = dict(nav=6000.0, core_value=0.0, satellite_value=2280.0, cash=3720.0)
    held = core_rebalance_order(cfg, days_since_rebalance=2, **kw)
    assert held.reason == "cadence_hold" and held.notional == 0.0
    go = core_rebalance_order(cfg, days_since_rebalance=5, **kw)
    assert go.reason == "band_deploy"
    assert round(go.notional, 2) == 3600.0  # (0.60 - 0.00) * 6000


def test_cadence_is_only_consulted_after_the_band_is_breached():
    """A book inside the band never touches the clock, so `days_since=0` on a
    freshly rebalanced book is a hold for the right reason."""
    cfg = core_sleeve_config(ON)
    order = core_rebalance_order(cfg, nav=6000.0, core_value=3600.0,
                                 satellite_value=2280.0, cash=120.0,
                                 days_since_rebalance=0)
    assert order.reason == "within_band"


def test_funding_bypasses_the_cadence_and_is_sized_to_the_shortfall():
    """The sleeve's original job. Sized off the shortfall, not off a cash
    target — sizing off a target is what makes today's release over-sell and
    then re-park on the next bar."""
    cfg = core_sleeve_config(ON)
    order = core_rebalance_order(cfg, nav=6000.0, core_value=3600.0,
                                 satellite_value=2280.0, cash=100.0,
                                 days_since_rebalance=0, funding_request=400.0)
    assert order.reason == "funding"
    assert round(order.notional, 2) == -300.0  # 400 needed - 100 on hand


def test_funding_already_covered_by_cash_emits_nothing():
    cfg = core_sleeve_config(ON)
    order = core_rebalance_order(cfg, nav=6000.0, core_value=3600.0,
                                 satellite_value=2280.0, cash=900.0,
                                 days_since_rebalance=99, funding_request=400.0)
    assert order.reason == "within_band"


def test_bear_derisk_bypasses_the_cadence():
    cfg = core_sleeve_config(ON)
    order = core_rebalance_order(cfg, nav=6000.0, core_value=3600.0,
                                 satellite_value=2280.0, cash=120.0,
                                 regime="bear", bear_dwell_days=4,
                                 days_since_rebalance=0)
    assert order.reason == "bear_derisk"
    assert round(order.notional, 2) == -1800.0  # 0.60 -> 0.30 of 6000


def test_circuit_blocks_adds_but_never_blocks_a_release():
    cfg = core_sleeve_config(ON)
    add = core_rebalance_order(cfg, nav=6000.0, core_value=0.0,
                               satellite_value=2280.0, cash=3720.0,
                               days_since_rebalance=99, circuit_tier="kill")
    assert add.reason == "circuit_blocks_add" and add.notional == 0.0
    cut = core_rebalance_order(cfg, nav=6000.0, core_value=5880.0,
                               satellite_value=0.0, cash=120.0,
                               regime="bear", bear_dwell_days=4,
                               days_since_rebalance=0, circuit_tier="kill")
    assert cut.notional < 0.0


def test_turnover_budget_blocks_buys_but_never_risk_reducing_sells():
    cfg = core_sleeve_config(ON)
    buy = core_rebalance_order(cfg, nav=6000.0, core_value=0.0,
                               satellite_value=2280.0, cash=3720.0,
                               days_since_rebalance=99, turnover_exhausted=True)
    assert buy.reason == "turnover_budget_exhausted" and buy.notional == 0.0
    sell = core_rebalance_order(cfg, nav=6000.0, core_value=5880.0,
                                satellite_value=0.0, cash=120.0,
                                regime="bear", bear_dwell_days=4,
                                days_since_rebalance=0, turnover_exhausted=True)
    assert sell.notional < 0.0


def test_dust_orders_are_refused_on_both_sides():
    """Alpaca rejects sub-$1 notional; 219 of 260 trades in bt 624314 were
    under $1. A backtest that fills them is not describing the account."""
    cfg = core_sleeve_config(dict(ON, core_rebalance_band_pct=0.0))
    tiny_buy = core_rebalance_order(cfg, nav=6000.0, core_value=3599.0,
                                    satellite_value=2280.0, cash=121.0,
                                    days_since_rebalance=99)
    assert tiny_buy.notional == 0.0 and tiny_buy.reason == "deploy_below_min"
    tiny_sell = core_rebalance_order(cfg, nav=6000.0, core_value=3601.0,
                                     satellite_value=2280.0, cash=119.0,
                                     days_since_rebalance=99)
    assert tiny_sell.notional == 0.0 and tiny_sell.reason == "release_below_min"


def test_deploy_is_capped_by_available_cash():
    cfg = core_sleeve_config(ON)
    order = core_rebalance_order(cfg, nav=6000.0, core_value=0.0,
                                 satellite_value=2280.0, cash=500.0,
                                 days_since_rebalance=99)
    assert round(order.notional, 2) == 500.0


def test_release_is_capped_by_the_position():
    cfg = core_sleeve_config(dict(ON, core_min_pct=0.0))
    order = core_rebalance_order(cfg, nav=6000.0, core_value=200.0,
                                 satellite_value=5700.0, cash=100.0,
                                 days_since_rebalance=99)
    assert order.notional >= -200.0


def test_zero_nav_is_a_no_op():
    cfg = core_sleeve_config(ON)
    assert core_rebalance_order(cfg, nav=0.0, core_value=0.0,
                                satellite_value=0.0, cash=0.0).reason == "no_nav"


# ── turnover budget ───────────────────────────────────────────────────────

def test_turnover_budget_off_by_default_even_when_core_is_on():
    cfg = core_sleeve_config(ON)
    assert cfg.turnover_budget_monthly_pct == 0.0
    assert turnover_budget_state(cfg, rolling_notional=1e9, nav=6000.0)[0] is False


def test_turnover_budget_flags_the_measured_baseline():
    """Live runs ~290%/month and the bull-window backtest ~466%/month; the
    Novy-Marx & Velikov line is 50%/month."""
    cfg = core_sleeve_config(dict(ON, turnover_budget_monthly_pct=0.50))
    exhausted, used = turnover_budget_state(
        cfg, rolling_notional=2.90 * 6000.0, nav=6000.0)
    assert exhausted is True
    assert round(used, 4) == 2.90
    ok, used_ok = turnover_budget_state(
        cfg, rolling_notional=0.38 * 6000.0, nav=6000.0)
    assert ok is False and round(used_ok, 4) == 0.38


# ── the arithmetic the design rests on ────────────────────────────────────

def test_target_structure_reproduces_the_designed_weights():
    """60/38/2 with the satellite full, and the measured 29.2% mean
    dead-or-short weight from bt 987397 goes to the 2% cash floor."""
    cfg = core_sleeve_config(ON)
    core = core_target_weight(cfg, satellite_weight=0.38)
    assert round(core + 0.38 + cfg.cash_floor_pct, 10) == 1.0
    assert core == 0.60


def test_min_deploy_floor_is_the_broker_floor_not_a_new_one():
    assert MIN_CORE_DEPLOY_USD == 50.0


# ── The satellite must not crowd out the core ────────────────────────────────
# Found by the first Layer-3 validation run (bt 786116): the SPY core sat at
# exactly 30.0% -- its clamp FLOOR -- for the whole window instead of the 60%
# target. Cause: core = clamp(1 - cash_floor - satellite, min, max) makes the
# core a RESIDUAL of the satellite, and the only thing bounding the satellite
# was nexus_portfolio_pct, which is measured against FULL NAV and therefore
# stops binding by construction once a core exists.

def _satellite_cap(config):
    """Mirror of the bound applied in graph_nexus_analysis's total-spend cap."""
    pct = float(config.get("nexus_portfolio_pct", 0.95) or 0.95)
    if bool(config.get("core_sleeve_enabled", False)):
        core = float(config.get("core_target_pct", 0.60) or 0.60)
        cash = float(config.get("cash_reserve_floor_pct", 0.02) or 0.02)
        room = max(0.05, 1.0 - core - cash)
        if room < pct:
            pct = room
    return pct


def test_satellite_cap_leaves_room_for_the_core_target():
    cfg = {"core_sleeve_enabled": True, "core_target_pct": 0.60,
           "cash_reserve_floor_pct": 0.02, "nexus_portfolio_pct": 0.95}
    assert abs(_satellite_cap(cfg) - 0.38) < 1e-9


def test_satellite_cap_is_untouched_when_the_core_is_off():
    """Byte-identical to today when the flag is off -- this ships dark."""
    cfg = {"core_sleeve_enabled": False, "nexus_portfolio_pct": 0.95}
    assert _satellite_cap(cfg) == 0.95


def test_a_tighter_operator_cap_still_wins():
    cfg = {"core_sleeve_enabled": True, "core_target_pct": 0.60,
           "cash_reserve_floor_pct": 0.02, "nexus_portfolio_pct": 0.20}
    assert _satellite_cap(cfg) == 0.20


def test_an_absurd_core_target_cannot_starve_the_satellite_to_zero():
    """A 99% core must not make the satellite unfundable and wedge the book."""
    cfg = {"core_sleeve_enabled": True, "core_target_pct": 0.99,
           "cash_reserve_floor_pct": 0.02, "nexus_portfolio_pct": 0.95}
    assert _satellite_cap(cfg) == 0.05
