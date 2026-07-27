from __future__ import annotations

import pytest

from live_readiness import (
    LiveReadinessError,
    ReadinessCheck,
    ReadinessReport,
    ReadinessState,
    assert_live_start_allowed,
    assert_readiness_transition_allowed,
    required_live_checks,
)


def test_production_artifact_cannot_start_with_unmet_calendar_gate():
    artifact_hash = "a" * 64
    report = ReadinessReport(
        instance_id="alpaca-main",
        state=ReadinessState.PAPER_ELIGIBLE,
        checks=tuple(
            ReadinessCheck(
                name=name,
                passed=name != "paper_observation",
                reason=(
                    "0 of 60 exact-build trading days"
                    if name == "paper_observation"
                    else "passed"
                ),
                evidence_hash=(str(index + 1) * 64)[:64],
            )
            for index, name in enumerate(required_live_checks())
        ),
        artifact_hash=artifact_hash,
    )

    with pytest.raises(LiveReadinessError, match="paper_observation"):
        assert_live_start_allowed(
            report,
            deployed_artifact_hash=artifact_hash,
        )


def test_live_running_requires_separate_explicit_activation():
    with pytest.raises(LiveReadinessError, match="explicit activation"):
        assert_readiness_transition_allowed(
            ReadinessState.LIVE_ELIGIBLE,
            ReadinessState.LIVE_RUNNING,
            evidence_state=ReadinessState.LIVE_RUNNING,
            explicit_activation=False,
        )


def test_exact_artifact_mismatch_blocks_even_complete_evidence():
    report = ReadinessReport(
        instance_id="alpaca-main",
        state=ReadinessState.LIVE_ELIGIBLE,
        checks=tuple(
            ReadinessCheck(name, True, "passed", "b" * 64)
            for name in required_live_checks()
        ),
        artifact_hash="a" * 64,
    )

    with pytest.raises(LiveReadinessError, match="artifact identity"):
        assert_live_start_allowed(
            report,
            deployed_artifact_hash="c" * 64,
        )
