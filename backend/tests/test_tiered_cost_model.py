"""Symbol-tiered execution costs.

The engine charges a flat 23.2 bps one-way on every symbol — a notional-weighted
spread measured on small-cap Nexus fills — and `equity_total_cost_bps` can only
stress UP (25/50). An ETF book measured that way is mis-priced by roughly 19 bps
a side, which is what mis-measured Strategy X by ~20pp.
"""
from datetime import datetime, timedelta, timezone

import pytest

from backend.backtest_evidence_options import (
    EvidenceOptionError,
    resolve_execution_cost_model,
    resolve_execution_cost_tiers,
    validate_evidence_options,
)
from backend.backtest_summary import assert_execution_provenance_promotable
from backend.portfolio_emulator import PortfolioEmulator, create_backtest_emulator
from backend.simulated_execution import (
    ETF_LIQUID_EQUITY_COST_MODEL,
    ETF_LIQUID_SYMBOLS,
    LIQUIDITY_ADJUSTED_EQUITY_COST_MODEL,
    ExecutionCostModel,
    NextEventExecutionSimulator,
    SimulationOrder,
    SimulationQuote,
    TieredExecutionCostModel,
    tiered_cost_model,
)

T0 = datetime(2026, 3, 2, 14, 0, tzinfo=timezone.utc)
T1 = datetime(2026, 3, 2, 15, 0, tzinfo=timezone.utc)
BASE = LIQUIDITY_ADJUSTED_EQUITY_COST_MODEL


def _tiered():
    return tiered_cost_model("etf-liquid", BASE)


# ── routing ─────────────────────────────────────────────────────────────────

def test_an_etf_routes_to_the_cheap_tier():
    model = _tiered()
    assert model.model_for("SPY") is ETF_LIQUID_EQUITY_COST_MODEL
    assert model.model_for("tqqq") is ETF_LIQUID_EQUITY_COST_MODEL


def test_everything_else_stays_on_the_measured_book_cost():
    assert _tiered().model_for("SNDK") is BASE


def test_the_preset_covers_every_leg_this_strategy_can_trade():
    assert {"SPY", "QQQ", "TQQQ", "QLD", "SQQQ", "BIL", "GLD", "IWM"} <= ETF_LIQUID_SYMBOLS


def test_the_etf_preset_is_four_point_four_bps_one_way():
    m = ETF_LIQUID_EQUITY_COST_MODEL
    assert m.spread_bps / 2.0 + m.slippage_bps + m.fee_bps == pytest.approx(4.4)


def test_the_composite_version_names_the_preset():
    assert _tiered().version == "equity-tiered-v1[etf-liquid]"


def test_the_tiered_model_delegates_its_scalars_to_the_default():
    """`SimulationQuote.from_mid(spread_bps=...)` and the promotion checker both
    read bare scalars off the model."""
    model = _tiered()
    assert model.spread_bps == BASE.spread_bps
    assert model.latency == BASE.latency


def test_an_unknown_preset_is_refused_at_construction():
    with pytest.raises(ValueError):
        tiered_cost_model("no-such-preset", BASE)


# ── provenance ──────────────────────────────────────────────────────────────

def _fill_one(cost_model, symbol):
    sim = NextEventExecutionSimulator(cost_model)
    sim.submit(SimulationOrder(order_id="o1", symbol=symbol, side="buy",
                               quantity=10.0, decision_at=T0,
                               execute_not_before=T0, source="main_signal"))
    fills = sim.on_quote(SimulationQuote.from_mid(
        symbol=symbol, timestamp=T1, mid=100.0, spread_bps=8.0))
    return sim, fills


def test_every_fill_stamps_the_composite_version_not_the_tiers():
    sim, fills = _fill_one(_tiered(), "SPY")
    assert fills
    assert fills[0].cost_model_version == "equity-tiered-v1[etf-liquid]"
    assert sim.execution_summary()["execution_cost_model_version"] == (
        "equity-tiered-v1[etf-liquid]")


def test_a_tiered_run_is_promotion_eligible():
    sim, _ = _fill_one(_tiered(), "SPY")
    assert_execution_provenance_promotable(sim.execution_summary())


def test_the_tier_actually_changes_the_fill_price():
    _, cheap = _fill_one(_tiered(), "SPY")
    _, dear = _fill_one(_tiered(), "SNDK")
    assert cheap[0].price < dear[0].price


def test_a_symbol_outside_every_tier_fills_byte_identically_to_the_flat_model():
    """With no matching tier the object graph must be indistinguishable, or
    every existing backtest silently changes."""
    _, tiered = _fill_one(_tiered(), "SNDK")
    _, flat = _fill_one(BASE, "SNDK")
    assert tiered[0].price == flat[0].price
    assert tiered[0].fees == flat[0].fees
    assert tiered[0].spread_cost == flat[0].spread_cost
    assert tiered[0].slippage_cost == flat[0].slippage_cost


def test_a_plain_cost_model_is_still_accepted_unchanged():
    sim, fills = _fill_one(BASE, "SPY")
    assert sim.execution_summary()["execution_cost_model_version"] == BASE.version
    assert fills[0].cost_model_version == BASE.version


def test_a_non_model_is_still_rejected():
    with pytest.raises(ValueError):
        NextEventExecutionSimulator("cheap")


# ── the emulator's legacy immediate path ────────────────────────────────────

def test_the_emulator_charges_the_tier_for_an_etf_and_the_book_cost_otherwise():
    emu = PortfolioEmulator(100_000.0, equity_cost_model=_tiered())
    etf_price, _, _, _ = emu._equity_fill("buy", 1.0, 100.0, symbol="SPY")
    other_price, _, _, _ = emu._equity_fill("buy", 1.0, 100.0, symbol="SNDK")
    assert etf_price < other_price


def test_the_emulator_stamps_the_composite_version_on_its_trades():
    emu = PortfolioEmulator(100_000.0, equity_cost_model=_tiered())
    assert emu.buy("SPY", 10.0, 100.0, timestamp=T0)
    assert emu.get_trade_history()[-1]["cost_model_version"] == (
        "equity-tiered-v1[etf-liquid]")


def test_create_backtest_emulator_accepts_a_tiered_model():
    emu = create_backtest_emulator(initial_cash=6_000.0, taker_fee=0.0,
                                   is_crypto=False,
                                   execution_delay=timedelta(days=1),
                                   cost_model=_tiered())
    assert emu.get_realism_summary()["equity_cost_model_version"] == (
        "equity-tiered-v1[etf-liquid]")


# ── the evidence option ─────────────────────────────────────────────────────

def test_the_option_is_absent_by_default():
    assert validate_evidence_options({})["equity_cost_tiers"] is None


def test_the_preset_is_accepted():
    assert validate_evidence_options(
        {"equity_cost_tiers": "etf-liquid"})["equity_cost_tiers"] == "etf-liquid"


def test_an_unknown_preset_is_refused():
    with pytest.raises(EvidenceOptionError):
        validate_evidence_options({"equity_cost_tiers": "cheap-please"})


def test_a_non_string_preset_is_refused():
    for junk in (1, True, ["etf-liquid"]):
        with pytest.raises(EvidenceOptionError):
            validate_evidence_options({"equity_cost_tiers": junk})


def test_no_preset_returns_the_base_model_object_itself():
    """Byte-identity for every existing run: not an equal object, the SAME one."""
    base = resolve_execution_cost_model(None)
    assert resolve_execution_cost_tiers(None, base) is base


def test_a_preset_wraps_the_stressed_model_not_the_nominal_one():
    """A cost stress arm must still mean what it says."""
    stressed = resolve_execution_cost_model(50.0)
    wrapped = resolve_execution_cost_tiers("etf-liquid", stressed)
    assert isinstance(wrapped, TieredExecutionCostModel)
    assert wrapped.default is stressed
