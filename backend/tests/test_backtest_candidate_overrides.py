"""Task 4 (2026-07-28): the evidence/cost/override contract carried on a
queued backtest row.

Three separate jobs live here because all three cross the API -> queue row ->
broker boundary and all three must fail CLOSED:

1. `validate_evidence_options` -- the queue-time contract. A non-off evidence
   mode must name its matrix, arm and cost scenario, and record/replay modes
   must name the artifact they build or consume. Broker credentials and
   arbitrary strategy keys are never accepted.
2. `apply_candidate_overrides` -- only the four approved A1-A4 candidate keys
   may reach the in-memory Graph Nexus spec, with their nested schemas
   validated. It must never mutate the caller's spec list.
3. `resolve_execution_cost_model` -- one immutable cost model per run.
   Stress scenarios scale by `target / (spread/2 + slippage + fee)` so the
   component proportions are preserved and the scenario is versioned.
"""
import os
import sys

import pytest

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from backtest_evidence_options import (  # noqa: E402
    CANDIDATE_OVERRIDE_KEYS,
    EvidenceOptionError,
    apply_candidate_overrides,
    resolve_execution_cost_model,
    validate_evidence_options,
)
from simulated_execution import (  # noqa: E402
    DEFAULT_EQUITY_EXECUTION_COST_MODEL,
    ExecutionCostModel,
)

_IDS = {
    "matrix_manifest_id": "matrix-sha256-" + "a" * 64,
    "matrix_arm_id": "arm-sha256-" + "b" * 64,
    "cost_scenario_id": "base",
}


# --------------------------------------------------------------- queue-time
def test_absent_options_are_byte_compatible():
    """An ordinary backtest POST carries none of these fields."""
    out = validate_evidence_options({})
    assert out["evidence_mode"] == "off"
    assert out["nexus_candidate_overrides"] == {}
    assert out["equity_total_cost_bps"] is None
    assert out["matrix_manifest_id"] is None
    assert validate_evidence_options(None) == out


def test_off_mode_rejects_evidence_identifiers():
    """Half-configured evidence is a bug, not a default."""
    with pytest.raises(EvidenceOptionError):
        validate_evidence_options({"evidence_mode": "off", **_IDS})


def test_unknown_mode_is_rejected():
    for bad in ("record_all", "REPLAY", "", 1, None, True):
        with pytest.raises(EvidenceOptionError):
            validate_evidence_options({"evidence_mode": bad})


def test_non_off_modes_require_matrix_arm_and_cost_scenario():
    for mode in ("record", "record_extend", "replay"):
        for missing in ("matrix_manifest_id", "matrix_arm_id", "cost_scenario_id"):
            payload = {"evidence_mode": mode, "fixture_build_id": "build-1",
                       "replay_fixture_id": "fixture-1", **_IDS}
            payload.pop(missing)
            with pytest.raises(EvidenceOptionError):
                validate_evidence_options(payload)


def test_record_modes_require_a_fixture_build_id():
    for mode in ("record", "record_extend"):
        with pytest.raises(EvidenceOptionError):
            validate_evidence_options({"evidence_mode": mode, **_IDS})
        out = validate_evidence_options(
            {"evidence_mode": mode, "fixture_build_id": "build-1", **_IDS})
        assert out["fixture_build_id"] == "build-1"
        assert out["replay_fixture_id"] is None


def test_replay_requires_a_sealed_fixture_id():
    with pytest.raises(EvidenceOptionError):
        validate_evidence_options({"evidence_mode": "replay", **_IDS})
    out = validate_evidence_options(
        {"evidence_mode": "replay", "replay_fixture_id": "fixture-1", **_IDS})
    assert out["replay_fixture_id"] == "fixture-1"


def test_credentials_are_never_accepted():
    for leak in ("key", "secret", "alpaca_key", "apiKey", "API_SECRET", "password"):
        with pytest.raises(EvidenceOptionError):
            validate_evidence_options({"evidence_mode": "off", leak: "x"})


def test_unknown_option_keys_are_rejected():
    with pytest.raises(EvidenceOptionError):
        validate_evidence_options({"evidence_mode": "off", "run_forever": True})


def test_cost_bps_accepts_only_nominal_25_or_50():
    assert validate_evidence_options({})["equity_total_cost_bps"] is None
    for good in (25, 50, 25.0, 50.0):
        assert validate_evidence_options(
            {"equity_total_cost_bps": good})["equity_total_cost_bps"] == float(good)
    for bad in (0, -25, 10, 100, "25", True, float("nan")):
        with pytest.raises(EvidenceOptionError):
            validate_evidence_options({"equity_total_cost_bps": bad})


# ------------------------------------------------------- candidate overrides
def test_only_the_four_approved_candidate_keys_exist():
    assert CANDIDATE_OVERRIDE_KEYS == frozenset({
        "regime_position_cap_recovery_hard_enforce",
        "momentum_breakout_max_nav_pct_by_regime",
        "deployment_ramp_caps_by_regime",
        "circuit_breaker_regime_adjustment_semantics_v2",
    })


def test_arbitrary_strategy_keys_are_rejected():
    for bad in ("max_positions", "deployment_bar1_cap_pct", "llm_api_key",
                "rotation_min_hold_days", "regime_profiles"):
        with pytest.raises(EvidenceOptionError):
            validate_evidence_options({"nexus_candidate_overrides": {bad: 1}})


def test_boolean_candidates_require_real_booleans():
    for key in ("regime_position_cap_recovery_hard_enforce",
                "circuit_breaker_regime_adjustment_semantics_v2"):
        assert validate_evidence_options(
            {"nexus_candidate_overrides": {key: True}}
        )["nexus_candidate_overrides"][key] is True
        for bad in (1, 0, "true", None, [], "yes"):
            with pytest.raises(EvidenceOptionError):
                validate_evidence_options({"nexus_candidate_overrides": {key: bad}})


def test_breakout_mapping_schema_is_validated():
    good = {"default": 0.06, "bull": 0.10, "recovery": 0.08}
    assert validate_evidence_options(
        {"nexus_candidate_overrides": {"momentum_breakout_max_nav_pct_by_regime": good}}
    )["nexus_candidate_overrides"]["momentum_breakout_max_nav_pct_by_regime"] == good
    for bad in ({"default": 0.06}, {"default": 0.06, "bull": 0.1, "recovery": 0},
                {"default": 0.06, "bull": 1.5, "recovery": 0.08},
                {"default": 0.06, "bull": 0.1, "recovery": 0.08, "extra": 0.2},
                {"default": 0.06, "bull": "0.1", "recovery": 0.08}, [], None, 0.06):
        with pytest.raises(EvidenceOptionError):
            validate_evidence_options({"nexus_candidate_overrides": {
                "momentum_breakout_max_nav_pct_by_regime": bad}})


def test_ramp_mapping_schema_is_validated():
    good = {"bull": [0.50, 0.70, 1.00]}
    assert validate_evidence_options(
        {"nexus_candidate_overrides": {"deployment_ramp_caps_by_regime": good}}
    )["nexus_candidate_overrides"]["deployment_ramp_caps_by_regime"] == good
    for bad in ({"bull": [0.5, 0.7]}, {"bull": [0.5, 0.7, 0.9, 1.0]},
                {"bull": [0.5, 0.7, 1.5]}, {"bull": [0.5, 0.7, 0]},
                {"chop": [0.5, 0.7, 0.9]}, {"bull": "0.5,0.7,1.0"}, {}, [], None):
        with pytest.raises(EvidenceOptionError):
            validate_evidence_options({"nexus_candidate_overrides": {
                "deployment_ramp_caps_by_regime": bad}})


def _specs():
    return [
        {"strategy": "graph_nexus_analysis", "config": {"max_positions": 14}},
        {"strategy": "position_sizing", "config": {"x": 1}},
    ]


def test_overrides_apply_to_the_nexus_spec_only():
    specs = _specs()
    out = apply_candidate_overrides(
        specs, {"circuit_breaker_regime_adjustment_semantics_v2": True})
    assert out[0]["config"]["circuit_breaker_regime_adjustment_semantics_v2"] is True
    assert out[0]["config"]["max_positions"] == 14, "base config survives"
    assert "circuit_breaker_regime_adjustment_semantics_v2" not in out[1]["config"]


def test_overrides_never_mutate_the_caller_specs():
    """The live Strategies document must never be touched — only an in-memory copy."""
    specs = _specs()
    original = [{"strategy": s["strategy"], "config": dict(s["config"])} for s in specs]
    apply_candidate_overrides(
        specs, {"regime_position_cap_recovery_hard_enforce": True})
    assert specs == original
    assert "regime_position_cap_recovery_hard_enforce" not in specs[0]["config"]


def test_empty_overrides_return_an_equal_spec_list():
    specs = _specs()
    assert apply_candidate_overrides(specs, {}) == specs
    assert apply_candidate_overrides(specs, None) == specs


def test_apply_rejects_unapproved_keys_defensively():
    """Second gate: even if a bad key reached the row, it stops at the broker."""
    with pytest.raises(EvidenceOptionError):
        apply_candidate_overrides(_specs(), {"max_positions": 99})


# ------------------------------------------------------------- cost scenarios
def _one_way(model):
    return model.spread_bps / 2.0 + model.slippage_bps + model.fee_bps


def test_nominal_scenario_returns_the_base_model_unchanged():
    model = resolve_execution_cost_model(None)
    assert model == DEFAULT_EQUITY_EXECUTION_COST_MODEL
    assert isinstance(model, ExecutionCostModel)


def test_stress_scenarios_hit_the_target_one_way_cost():
    for target in (25.0, 50.0):
        model = resolve_execution_cost_model(target)
        assert _one_way(model) == pytest.approx(target)


def test_stress_scenarios_preserve_component_proportions():
    base = DEFAULT_EQUITY_EXECUTION_COST_MODEL
    model = resolve_execution_cost_model(50.0)
    scale = _one_way(model) / _one_way(base)
    assert model.spread_bps == pytest.approx(base.spread_bps * scale)
    assert model.slippage_bps == pytest.approx(base.slippage_bps * scale)
    assert model.fee_bps == pytest.approx(base.fee_bps * scale)
    assert model.latency == base.latency


def test_stress_scenarios_are_versioned_distinctly():
    versions = {resolve_execution_cost_model(t).version for t in (None, 25.0, 50.0)}
    assert len(versions) == 3
    assert resolve_execution_cost_model(25.0).version != DEFAULT_EQUITY_EXECUTION_COST_MODEL.version


def test_cost_model_resolution_is_deterministic():
    """The SAME object contract must reach preregistration and the emulator."""
    assert resolve_execution_cost_model(25.0) == resolve_execution_cost_model(25.0)


def test_invalid_cost_target_is_rejected():
    for bad in (0, -1, "25", float("inf"), True):
        with pytest.raises(EvidenceOptionError):
            resolve_execution_cost_model(bad)
