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
    run_registered_experiment,
    walk_forward_splits,
)
from experiment_registry import (
    ExperimentRegistry as ImmutableExperimentRegistry,
    ExperimentSpec as ImmutableExperimentSpec,
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


def _immutable_spec(experiment_id):
    created_at = "2026-07-27T06:30:00+00:00"

    def source_manifest(manifest_id, source, content_hash):
        return {
            "manifest_id": manifest_id,
            "source_hashes": {source: content_hash},
            "created_at": created_at,
        }

    return ImmutableExperimentSpec(
        experiment_id=experiment_id,
        parent_experiment_id=None,
        search_scope="fresh-direct/live-40",
        commit_sha="b25e2f1",
        source_tree_hash="sha256:source",
        effective_config={"active_ceiling": 0.40},
        model_provider="openai",
        model_name="research-model",
        prompt_hashes={"decision": "sha256:prompt"},
        model_settings={"temperature": 0.0},
        seed=179,
        predeclared_repeats=1,
        dataset_manifest=source_manifest(
            "dataset", "bars", "sha256:data"
        ),
        graph_manifest=source_manifest(
            "graph", "graph", "sha256:graph"
        ),
        universe_manifest=source_manifest(
            "universe", "universe", "sha256:universe"
        ),
        benchmark_manifest={
            "manifest_id": "spy",
            "symbol": "SPY",
            "timeframe": "1Day",
            "adjustment": "all",
            "price_field": "c",
            "total_return": True,
            "feed": "iex",
            "start_date": "2024-01-02",
            "end_date": "2026-06-30",
            "valuation_rule": "xnys_session_close",
            "valuation_timestamps": [
                "2024-01-02T21:00:00Z",
                "2026-06-30T20:00:00Z",
            ],
            "content_hash": "spy-sha256-" + "4" * 64,
        },
        execution_cost_model={
            "version": "cost-v1",
            "spread_bps": 1.0,
            "slippage_bps": 1.0,
            "fee_bps": 0.0,
            "latency_seconds": 0.0,
        },
        start_date="2024-01-02",
        end_date="2026-06-30",
        fold="fold-1",
        actor="test",
    )


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


def test_registered_runner_persists_attempt_before_invoking_model():
    events = []

    class RecordingStore:
        def __init__(self):
            from experiment_registry import InMemoryExperimentStore

            self.inner = InMemoryExperimentStore()

        def insert_experiment_registration(self, registration):
            events.append(("registered", registration.experiment_id))
            return self.inner.insert_experiment_registration(registration)

        def insert_experiment_outcome(self, outcome):
            events.append((outcome.status, outcome.experiment_id))
            return self.inner.insert_experiment_outcome(outcome)

        def __getattr__(self, name):
            return getattr(self.inner, name)

    spec = _immutable_spec("attempt-before-model")
    registry = ImmutableExperimentRegistry(store=RecordingStore())

    def model_call():
        events.append(("model", spec.experiment_id))
        return {"active_return": 0.12}

    result = run_registered_experiment(registry, spec, model_call)

    assert result == {"active_return": 0.12}
    assert events == [
        ("registered", "attempt-before-model"),
        ("model", "attempt-before-model"),
        ("completed", "attempt-before-model"),
    ]


def test_registered_runner_records_failure_without_exception_payload():
    spec = _immutable_spec("failed-model-attempt")
    registry = ImmutableExperimentRegistry()

    def failing_model_call():
        raise RuntimeError("SENSITIVE-PROVIDER-PAYLOAD")

    with pytest.raises(RuntimeError, match="SENSITIVE-PROVIDER"):
        run_registered_experiment(registry, spec, failing_model_call)

    outcome = registry.outcome(spec.experiment_id)
    assert outcome.status == "failed"
    assert outcome.failure_reason == "RuntimeError"


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
