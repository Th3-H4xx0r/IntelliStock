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


# ── the next-event path end to end ──────────────────────────────────────────

def _next_event_fill(cost_model, symbol):
    """One fill through the emulator's REAL next-event path, which builds the
    quote itself — the spread it chooses has to follow the symbol too, or the
    tier is applied to the fill but not to the book it fills against."""
    emu = create_backtest_emulator(initial_cash=100_000.0, taker_fee=0.0,
                                   is_crypto=False,
                                   execution_delay=timedelta(days=1),
                                   cost_model=cost_model)
    emu.record_order(SimulationOrder(
        order_id="o1", symbol=symbol, side="buy", quantity=10.0,
        decision_at=T0, execute_not_before=T0 + timedelta(days=1),
        source="main_signal"))
    emu.process_price_event({symbol: 100.0}, timestamp=T0 + timedelta(days=2))
    return emu.get_execution_summary()["fill_provenance"][0]


def test_the_next_event_path_charges_the_tier_for_an_etf():
    fill = _next_event_fill(_tiered(), "SPY")
    # 8.0/2 + 0.1 bps on a 100.0 mid.
    assert fill["price"] == pytest.approx(100.041, abs=1e-6)
    assert fill["cost_model_version"] == "equity-tiered-v1[etf-liquid]"


def test_the_next_event_path_leaves_an_untiered_symbol_byte_identical():
    tiered = _next_event_fill(_tiered(), "SNDK")
    flat = _next_event_fill(BASE, "SNDK")
    for key in ("price", "fees", "spread_cost", "slippage_cost"):
        assert tiered[key] == flat[key]


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


# ── the queue row ───────────────────────────────────────────────────────────

def test_a_tiers_only_run_keeps_its_evidence_block_on_the_queue_row():
    """`action_create_backtest` only stamps the evidence block when the run
    declares something. A tiers-only run declares exactly one thing, and
    dropping it there loses the cost basis silently on the way to the broker —
    which is the defect `pit_mode` already hit once
    (test_backtest_research_default.py). Asserted against the source for the
    same reason that test is: the gate is inline in a function that needs a
    live queue connection.
    """
    import os

    backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src = open(os.path.join(backend_dir, "interactive_utils.py")).read()
    start = src.index("def action_create_backtest(")
    body = src[start:src.index("\ndef ", start + 10)]
    assert '_evidence["equity_cost_tiers"] is not None' in body


# ── tiers are not preregisterable ───────────────────────────────────────────

_RECORD = {
    "evidence_mode": "record",
    "fixture_build_id": "build-eb-1",
    "matrix_manifest_id": "m1",
    "matrix_arm_id": "a1",
    "cost_scenario_id": "base",
}


def test_a_matrix_arm_may_not_declare_tiers():
    """A matrix arm preregisters ONE cost model and its receipt compares the
    EXECUTED hash against it. A tier wraps that model, so a tiered arm hashes
    to no preregistered scenario and finishes silently ineligible -- after
    burning the whole window. Refuse it at queue time instead."""
    for mode in ("record", "record_extend", "replay"):
        payload = dict(_RECORD, evidence_mode=mode,
                       replay_fixture_id="fixture-1",
                       equity_cost_tiers="etf-liquid")
        with pytest.raises(EvidenceOptionError) as excinfo:
            validate_evidence_options(payload)
        assert "evidence_mode off" in str(excinfo.value)


def test_a_matrix_arm_without_tiers_is_still_accepted():
    """The gate must not break ordinary evidence runs."""
    assert validate_evidence_options(_RECORD)["equity_cost_tiers"] is None


def test_tiers_are_still_accepted_on_an_off_run():
    assert validate_evidence_options(
        {"evidence_mode": "off",
         "equity_cost_tiers": "etf-liquid"})["equity_cost_tiers"] == "etf-liquid"


# ── the per-tier cost breakdown ─────────────────────────────────────────────

def test_a_tiered_emulator_reports_every_tiers_one_way_cost():
    """`equity_one_way_cost_bps` is the DEFAULT tier's cost, so an all-ETF run
    would be read off 23.2 bps when it paid 4.4."""
    summary = PortfolioEmulator(
        100_000.0, equity_cost_model=_tiered()).get_realism_summary()
    assert summary["equity_cost_tiers_active"] is True
    assert summary["equity_one_way_cost_bps_by_tier"] == {
        "default": pytest.approx(23.2),
        "etf-liquid": pytest.approx(4.4),
    }
    assert summary["equity_one_way_cost_bps"] == pytest.approx(23.2)


def test_an_untiered_emulator_reports_one_tier_and_says_so():
    summary = PortfolioEmulator(
        100_000.0, equity_cost_model=BASE).get_realism_summary()
    assert summary["equity_cost_tiers_active"] is False
    assert summary["equity_one_way_cost_bps_by_tier"] == {
        "default": pytest.approx(23.2)}


def test_the_breakdown_is_computed_from_each_tiers_own_model():
    """Not hardcoded: it must follow a stressed default."""
    wrapped = resolve_execution_cost_tiers(
        "etf-liquid", resolve_execution_cost_model(50.0))
    by_tier = wrapped.one_way_cost_bps_by_tier()
    assert by_tier["default"] == pytest.approx(50.0)
    assert by_tier["etf-liquid"] == pytest.approx(4.4)


# ── the evidence block is served, not silently dropped ──────────────────────

def test_the_serving_whitelist_carries_the_tier():
    """`_EVIDENCE_SUMMARY_FIELDS` is a whitelist, so an option missing from it
    is dropped silently on the way to /backtests/{id}/summary."""
    import interactive_utils

    assert "equity_cost_tiers" in interactive_utils._EVIDENCE_SUMMARY_FIELDS
    served = interactive_utils._evidence_summary_projection(
        {"evidence": {"evidence_mode": "off", "equity_cost_tiers": "etf-liquid"}})
    assert served["equity_cost_tiers"] == "etf-liquid"


def test_the_results_side_projection_carries_the_tier():
    """The block stored on the BacktestResults row, which the whitelist above
    then serves from. Inert while tiers require evidence_mode=off -- this
    projection only runs for a NON-off run -- but it is the same
    grow-with-the-option list, and forgetting it fails silently rather than
    loudly."""
    from backtest_evidence_runtime import EvidenceRunLifecycle

    lifecycle = EvidenceRunLifecycle(
        options={"evidence_mode": "off", "equity_cost_tiers": "etf-liquid"},
        backtest_id="bt-1", store=None, window=None, fixture_ordinal=0,
        benchmark_manifest={}, rng_seed_manifest={})
    assert lifecycle.summary_projection()["equity_cost_tiers"] == "etf-liquid"


# ── the snapshot contract's declared key set ────────────────────────────────

_V1_EVIDENCE = {
    "evidence_mode": "off", "fixture_build_id": None, "replay_fixture_id": None,
    "matrix_manifest_id": None, "matrix_arm_id": None, "cost_scenario_id": None,
    "equity_total_cost_bps": None, "nexus_candidate_overrides": {},
    "fixture_ordinal": None, "pit_mode": "research",
}


def test_the_v1_snapshot_still_validates_its_own_evidence_block():
    """Adding an option to the validator must not invalidate signed v1 bodies."""
    import backtest_execution_snapshot as snap

    snap._validate_evidence(dict(_V1_EVIDENCE), "$.core.run.evidence")


def test_a_validator_that_drops_a_declared_key_fails_the_snapshot(monkeypatch):
    """Comparing with `.get()` alone would read None on both sides and pass."""
    import backtest_evidence_options as opts
    import backtest_execution_snapshot as snap

    real = opts.validate_evidence_options
    monkeypatch.setattr(
        opts, "validate_evidence_options",
        lambda payload: {k: v for k, v in real(payload).items()
                         if k != "pit_mode"})
    with pytest.raises(snap.ExecutionSnapshotError) as excinfo:
        snap._validate_evidence(dict(_V1_EVIDENCE), "$.core.run.evidence")
    assert excinfo.value.code == "schema_evidence_invalid"


# ── the guards name what they accept ────────────────────────────────────────

def test_the_guards_name_both_accepted_types():
    """An operator who passes the wrong thing should not be told the tiered
    model is unacceptable when it is."""
    for build in (lambda: NextEventExecutionSimulator("cheap"),
                  lambda: PortfolioEmulator(1.0, equity_cost_model="cheap"),
                  lambda: create_backtest_emulator(
                      initial_cash=1.0, taker_fee=0.0, is_crypto=False,
                      execution_delay=timedelta(days=1), cost_model="cheap")):
        with pytest.raises(ValueError) as excinfo:
            build()
        assert "TieredExecutionCostModel" in str(excinfo.value)
