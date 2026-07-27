from dataclasses import FrozenInstanceError

import pytest


def test_live_start_refuses_an_unmet_check():
    from live_readiness import (
        LiveReadinessError,
        ReadinessCheck,
        ReadinessReport,
        ReadinessState,
        assert_live_start_allowed,
    )

    report = ReadinessReport(
        instance_id="test-instance",
        state=ReadinessState.RESEARCH,
        checks=(ReadinessCheck("secrets", False, "plaintext", "abc"),),
        artifact_hash="artifact-hash",
    )

    with pytest.raises(LiveReadinessError, match="secrets"):
        assert_live_start_allowed(report)


def test_live_start_requires_every_passing_check_and_an_eligible_state():
    from live_readiness import (
        LiveReadinessError,
        ReadinessCheck,
        ReadinessReport,
        ReadinessState,
        assert_live_start_allowed,
    )

    report = ReadinessReport(
        instance_id="test-instance",
        state=ReadinessState.PAPER_ELIGIBLE,
        checks=(ReadinessCheck("evidence", True, "complete", "abc"),),
        artifact_hash="artifact-hash",
    )

    with pytest.raises(LiveReadinessError, match="state"):
        assert_live_start_allowed(report)


def test_readiness_records_are_immutable_and_fingerprint_checked():
    from live_readiness import (
        LiveReadinessError,
        ReadinessCheck,
        ReadinessReport,
        ReadinessState,
        report_fingerprint,
        report_from_mapping,
    )

    report = ReadinessReport(
        instance_id="test-instance",
        state=ReadinessState.LIVE_ELIGIBLE,
        checks=(ReadinessCheck("secrets", True, "encrypted", "abc"),),
        artifact_hash="artifact-hash",
    )
    with pytest.raises(FrozenInstanceError):
        report.artifact_hash = "changed"

    payload = {
        "instance_id": report.instance_id,
        "state": report.state.value,
        "artifact_hash": report.artifact_hash,
        "checks": [{"name": "secrets", "passed": True,
                    "reason": "encrypted", "evidence_hash": "abc"}],
        "fingerprint": report_fingerprint(report),
    }
    assert report_from_mapping(payload, instance_id="test-instance") == report
    payload["fingerprint"] = "not-a-fingerprint"
    with pytest.raises(LiveReadinessError, match="fingerprint"):
        report_from_mapping(payload, instance_id="test-instance")


def test_inactive_verifier_rejects_any_order_delta():
    from scripts.verify_inactive_deployment import (
        AccountInvariant,
        compare_account_invariants,
    )

    before = AccountInvariant.from_docs(positions=[], orders=[])
    after = AccountInvariant.from_docs(positions=[], orders=[{"id": "new"}])

    assert compare_account_invariants(before, after).passed is False


def test_inactive_verifier_has_no_broker_write_imports():
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1] / "scripts" /
              "verify_inactive_deployment.py").read_text()
    prohibited = ("import broker", "from broker", "submit_order", "cancel_order",
                  "replace_order", "close_position")
    assert not any(token in source for token in prohibited)
