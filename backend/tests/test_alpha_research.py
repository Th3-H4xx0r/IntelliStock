"""Task 16: registered purged walk-forward research + Stage B STOP/GO."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from benchmark_alpha.research import (
    DataManifest,
    ExperimentRegistry,
    ExperimentSpec,
    SealedHoldout,
    StageBGoReport,
    evaluate_stage_b_go,
    walk_forward_splits,
)

DATES = [f"2024-{m:02d}-15" for m in range(1, 13)] + \
        [f"2025-{m:02d}-15" for m in range(1, 13)] + \
        [f"2026-{m:02d}-15" for m in range(1, 7)]


def _spec(**kw):
    base = dict(model_family="graph_fresh_direct", horizons=(1, 3, 5),
                active_ceiling=0.40, train_months=12, calibration_months=3,
                test_months=3, embargo_days=5, data_snapshot_id="snap-1",
                code_hash="c1", model_hash="m1", cost_model_version="cm-1")
    base.update(kw)
    return ExperimentSpec(**base)


def test_splits_are_ordered_purged_and_embargoed():
    splits = walk_forward_splits(DATES)
    assert splits
    for split in splits:
        assert max(split.train_dates) < min(split.calibration_dates)
        assert max(split.calibration_dates) < min(split.test_dates)
        # 5-day embargo between calibration end and test start.
        from datetime import date
        cal_end = date.fromisoformat(max(split.calibration_dates))
        test_start = date.fromisoformat(min(split.test_dates))
        assert (test_start - cal_end).days > 5
    starts = [min(s.test_dates) for s in splits]
    assert starts == sorted(starts)


def test_experiment_id_is_deterministic_and_duplicates_return_original():
    assert _spec().experiment_id == _spec().experiment_id
    assert _spec().experiment_id != _spec(active_ceiling=0.60).experiment_id
    registry = ExperimentRegistry()
    first = registry.register(_spec())
    again = registry.register(_spec())
    assert first == again
    assert len(registry.all_experiments()) == 1


def test_failed_experiments_remain_queryable_and_count_as_trials():
    registry = ExperimentRegistry()
    eid = registry.register(_spec())
    registry.mark_running(eid)
    registry.mark_failed(eid, "data outage")
    rows = registry.all_experiments()
    assert rows[0]["status"] == "failed"
    assert registry.trial_count() == 1


def test_manifest_refuses_unprovable_features_and_hashes_content():
    manifest = DataManifest()
    manifest.register("bars:AAPL", content_hash="h1", known_at="2026-07-01")
    with pytest.raises(ValueError):
        manifest.register("news:current", content_hash="h2", known_at=None)
    with pytest.raises(ValueError):
        manifest.register("graph:live", content_hash=None, known_at="2026-07-01")
    frozen = manifest.freeze()
    assert frozen.manifest_hash == manifest.freeze().manifest_hash
    manifest.register("bars:MSFT", content_hash="h3", known_at="2026-07-02")
    assert manifest.freeze().manifest_hash != frozen.manifest_hash


def test_sealed_holdout_evaluates_exactly_once_per_family():
    holdout = SealedHoldout("holdout-2026H2")
    holdout.evaluate("graph_fresh_direct")
    with pytest.raises(Exception):
        holdout.evaluate("graph_fresh_direct")
    holdout.evaluate("deterministic_challenger")  # separate family allowed


def _report(**kw):
    base = dict(model_family="graph_fresh_direct", active_ceiling=0.40,
                median_annual_active_pp=9.0, target_annual_active_pp=11.0,
                bootstrap_lower_bound=0.5, information_ratio=0.9,
                deflated_sharpe_probability=0.96, max_drawdown_magnitude=0.10,
                profit_factor_after_costs=1.3, positive_quarter_fraction=0.7,
                regimes_covered=3, unseen_sample_months=24)
    base.update(kw)
    return StageBGoReport(**base)


def test_stage_b_gate_passes_only_a_complete_fresh_direct_report():
    decision = evaluate_stage_b_go(_report())
    assert decision.go is True
    assert decision.grants == "build_tasks_12_15_only"  # never trading


@pytest.mark.parametrize("field,value", [
    ("median_annual_active_pp", 7.9),
    ("target_annual_active_pp", 9.9),
    ("bootstrap_lower_bound", -0.1),
    ("information_ratio", 0.74),
    ("deflated_sharpe_probability", 0.9499),
    ("max_drawdown_magnitude", 0.1501),
    ("profit_factor_after_costs", 1.0),
    ("positive_quarter_fraction", 0.59),
    ("regimes_covered", 2),
    ("unseen_sample_months", 11),
])
def test_every_threshold_fails_the_gate_individually(field, value):
    decision = evaluate_stage_b_go(_report(**{field: value}))
    assert decision.go is False
    assert any(field in reason for reason in decision.reasons)


def test_deterministic_only_success_is_no_go():
    decision = evaluate_stage_b_go(_report(model_family="deterministic_challenger"))
    assert decision.go is False
    assert any("fresh_direct" in r for r in decision.reasons)


def test_negative_drawdown_is_a_schema_error_not_a_pass():
    with pytest.raises(ValueError):
        _report(max_drawdown_magnitude=-0.20)
