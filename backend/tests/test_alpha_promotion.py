from dataclasses import replace
from datetime import datetime, timezone

import pytest

from benchmark_alpha.promotion import (
    InMemoryPromotionStore,
    PromotionEvidence,
    PromotionRecordError,
    evaluate_promotion,
    record_promotion,
    readiness_report_from_promotion,
)
from live_readiness import ReadinessState, assert_live_start_allowed


DIGESTS = {
    name: (character * 64)
    for name, character in zip(
        (
            "artifact",
            "config",
            "model",
            "data",
            "cost",
            "risk",
            "adapter",
        ),
        "abcdef1",
    )
}


def passing_evidence(**changes):
    values = dict(
        instance_id="alpaca-main",
        artifact_hash=DIGESTS["artifact"],
        deployed_artifact_hash=DIGESTS["artifact"],
        paper_artifact_hash=DIGESTS["artifact"],
        config_hash=DIGESTS["config"],
        observed_config_hash=DIGESTS["config"],
        paper_config_hash=DIGESTS["config"],
        model_hash=DIGESTS["model"],
        observed_model_hash=DIGESTS["model"],
        data_manifest_hash=DIGESTS["data"],
        observed_data_manifest_hash=DIGESTS["data"],
        cost_model_hash=DIGESTS["cost"],
        risk_policy_hash=DIGESTS["risk"],
        broker_adapter_hash=DIGESTS["adapter"],
        point_in_time_months=24,
        unseen_months=12,
        regime_count=3,
        purged_fold_count=4,
        sealed_holdout_preregistered=True,
        sealed_holdout_evaluations=1,
        aligned_spy_total_return=True,
        costed_fills=True,
        registered_trial_count=12,
        repeats_predeclared=True,
        median_repeat_used=True,
        median_annual_active_pp=8.0,
        target_annual_active_pp=10.0,
        bootstrap_active_low=0.0001,
        information_ratio=0.75,
        deflated_sharpe_probability=0.95,
        max_drawdown_magnitude=0.15,
        beta=1.0,
        profit_factor_after_costs=1.0001,
        positive_unseen_quarter_fraction=0.60,
        parameter_stability_passed=True,
        leave_one_winner_out_passed=True,
        concentration_analysis_passed=True,
        ci_passed=True,
        lifecycle_chaos_passed=True,
        dependency_chaos_passed=True,
        restart_state_passed=True,
        secret_migration_rehearsed=True,
        rollback_rehearsed=True,
        plaintext_credentials_found=0,
        unresolved_high_critical_findings=0,
        unresolved_ownership=0,
        watchdog_healthy=True,
        watchdog_age_seconds=1.0,
        degraded_audit_intervals=0,
        exact_build_paper=True,
        paper_trading_days=60,
        point_in_time_provenance_verified=True,
    )
    values.update(changes)
    return PromotionEvidence(**values)


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("point_in_time_months", 23, "history_months"),
        (
            "point_in_time_provenance_verified",
            False,
            "point_in_time_provenance",
        ),
        ("unseen_months", 11, "unseen_months"),
        ("regime_count", 2, "regime_count"),
        ("purged_fold_count", 0, "purged_folds"),
        ("sealed_holdout_preregistered", False, "sealed_holdout"),
        ("sealed_holdout_evaluations", 2, "sealed_holdout"),
        ("aligned_spy_total_return", False, "benchmark_alignment"),
        ("costed_fills", False, "costed_fills"),
        ("registered_trial_count", 0, "trial_count"),
        ("repeats_predeclared", False, "predeclared_repeats"),
        ("median_repeat_used", False, "median_repeat"),
        ("median_annual_active_pp", 7.99, "median_active"),
        ("target_annual_active_pp", 9.99, "target_active"),
        ("bootstrap_active_low", 0.0, "bootstrap"),
        ("information_ratio", 0.74, "information_ratio"),
        ("deflated_sharpe_probability", 0.949, "deflated_sharpe"),
        ("max_drawdown_magnitude", 0.1501, "max_drawdown"),
        ("beta", 1.11, "beta"),
        ("profit_factor_after_costs", 1.0, "profit_factor"),
        (
            "positive_unseen_quarter_fraction",
            0.59,
            "unseen_quarters",
        ),
        ("parameter_stability_passed", False, "parameter_stability"),
        ("leave_one_winner_out_passed", False, "leave_one_winner_out"),
        (
            "concentration_analysis_passed",
            False,
            "concentration_analysis",
        ),
        ("ci_passed", False, "ci"),
        ("lifecycle_chaos_passed", False, "lifecycle_chaos"),
        ("dependency_chaos_passed", False, "dependency_chaos"),
        ("restart_state_passed", False, "restart_state"),
        ("secret_migration_rehearsed", False, "secret_migration"),
        ("rollback_rehearsed", False, "rollback"),
        ("plaintext_credentials_found", 1, "plaintext_credentials"),
        (
            "unresolved_high_critical_findings",
            1,
            "unresolved_findings",
        ),
        ("unresolved_ownership", 1, "ownership"),
        ("watchdog_healthy", False, "watchdog"),
        ("watchdog_age_seconds", 61.0, "watchdog"),
        ("degraded_audit_intervals", 1, "degraded_audit"),
        ("exact_build_paper", False, "paper_build"),
        ("paper_trading_days", 59, "paper_days"),
    ],
)
def test_each_gate_fails_closed(field, value, reason):
    decision = evaluate_promotion(
        replace(passing_evidence(), **{field: value})
    )
    assert decision.passed is False
    assert reason in decision.reasons


@pytest.mark.parametrize(
    ("field", "other_field", "reason"),
    [
        ("deployed_artifact_hash", "artifact_hash", "artifact_mismatch"),
        ("paper_artifact_hash", "artifact_hash", "paper_build"),
        ("observed_config_hash", "config_hash", "config_mismatch"),
        ("paper_config_hash", "config_hash", "paper_config"),
        ("observed_model_hash", "model_hash", "model_mismatch"),
        (
            "observed_data_manifest_hash",
            "data_manifest_hash",
            "data_mismatch",
        ),
    ],
)
def test_artifact_and_evidence_identity_must_match(field, other_field, reason):
    evidence = passing_evidence()
    broken = replace(evidence, **{field: "9" * 64})
    assert reason in evaluate_promotion(broken).reasons


def test_complete_evidence_can_be_live_eligible_but_never_live_running():
    evidence = passing_evidence()
    decision = evaluate_promotion(evidence)
    assert decision.passed is True
    assert decision.eligible_state is ReadinessState.LIVE_ELIGIBLE
    report = readiness_report_from_promotion(evidence, decision)
    assert report.state is ReadinessState.LIVE_ELIGIBLE
    assert_live_start_allowed(
        report, deployed_artifact_hash=evidence.artifact_hash
    )
    assert report.state is not ReadinessState.LIVE_RUNNING


def test_truthy_non_boolean_cannot_claim_verified_pit_provenance():
    evidence = replace(
        passing_evidence(),
        point_in_time_provenance_verified="true",
    )

    assert "point_in_time_provenance" in evaluate_promotion(evidence).reasons


def test_missing_calendar_observation_remains_paper_eligible_not_live():
    evidence = passing_evidence(paper_trading_days=0)
    decision = evaluate_promotion(evidence)
    assert decision.eligible_state is ReadinessState.PAPER_ELIGIBLE
    assert "paper_days" in decision.reasons
    report = readiness_report_from_promotion(evidence, decision)
    assert report.state is ReadinessState.PAPER_ELIGIBLE
    assert report.checks[-1].name == "paper_observation"
    assert report.checks[-1].passed is False


def test_promotion_record_requires_explicit_authenticated_approval():
    evidence = passing_evidence()
    decision = evaluate_promotion(evidence)
    store = InMemoryPromotionStore()
    for operator, approved in (("", True), ("operator", False)):
        with pytest.raises(PromotionRecordError):
            record_promotion(
                store,
                evidence,
                decision,
                operator_id=operator,
                approved=approved,
                created_at=datetime.now(timezone.utc),
            )
    record = record_promotion(
        store,
        evidence,
        decision,
        operator_id="operator",
        approved=True,
        created_at=datetime(2026, 7, 27, 12, tzinfo=timezone.utc),
    )
    assert store.get(record.record_id) == record.to_doc()
    assert "operator_id='operator'" not in repr(record)
    assert record.operator_id_hash != "operator"


def test_failed_decision_cannot_be_recorded_as_promotion():
    evidence = passing_evidence(paper_trading_days=0)
    with pytest.raises(PromotionRecordError):
        record_promotion(
            InMemoryPromotionStore(),
            evidence,
            evaluate_promotion(evidence),
            operator_id="operator",
            approved=True,
            created_at=datetime.now(timezone.utc),
        )
