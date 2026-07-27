from dataclasses import FrozenInstanceError

import pytest


def test_live_start_refuses_an_unmet_check():
    from live_readiness import (
        LiveReadinessError,
        ReadinessCheck,
        ReadinessReport,
        ReadinessState,
        assert_live_start_allowed, required_live_checks,
    )

    report = ReadinessReport(
        instance_id="test-instance",
        state=ReadinessState.RESEARCH,
        checks=tuple(ReadinessCheck(name, name != "secrets", "plaintext", "a" * 64)
                     for name in required_live_checks()),
        artifact_hash="b" * 64,
    )

    with pytest.raises(LiveReadinessError, match="secrets"):
        assert_live_start_allowed(report, deployed_artifact_hash="b" * 64)


def test_live_start_requires_every_passing_check_and_an_eligible_state():
    from live_readiness import (
        LiveReadinessError,
        ReadinessCheck,
        ReadinessReport,
        ReadinessState,
        assert_live_start_allowed, required_live_checks,
    )

    report = ReadinessReport(
        instance_id="test-instance",
        state=ReadinessState.PAPER_ELIGIBLE,
        checks=tuple(ReadinessCheck(name, True, "complete", "a" * 64)
                     for name in required_live_checks()),
        artifact_hash="b" * 64,
    )

    with pytest.raises(LiveReadinessError, match="state"):
        assert_live_start_allowed(report, deployed_artifact_hash="b" * 64)


def test_readiness_records_are_immutable_and_fingerprint_checked():
    from live_readiness import (
        LiveReadinessError,
        ReadinessCheck,
        ReadinessReport,
        ReadinessState,
        report_fingerprint, required_live_checks,
        report_from_mapping,
    )

    report = ReadinessReport(
        instance_id="test-instance",
        state=ReadinessState.LIVE_ELIGIBLE,
        checks=tuple(ReadinessCheck(name, True, "encrypted", "a" * 64)
                     for name in required_live_checks()),
        artifact_hash="b" * 64,
    )
    with pytest.raises(FrozenInstanceError):
        report.artifact_hash = "changed"

    payload = {
        "instance_id": report.instance_id,
        "state": report.state.value,
        "artifact_hash": report.artifact_hash,
        "checks": [c.__dict__ for c in report.checks],
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


def test_strict_report_rejects_coercions_empty_duplicate_missing_and_bad_hashes():
    from live_readiness import LiveReadinessError, required_live_checks, report_from_mapping

    digest = "a" * 64
    checks = [{"name": name, "passed": True, "reason": "verified",
               "evidence_hash": digest} for name in required_live_checks()]
    payload = {"instance_id": "test-instance", "state": "LIVE_ELIGIBLE",
               "artifact_hash": digest, "checks": checks, "fingerprint": digest}
    for bad in ("false", 0, 1, None):
        broken = {**payload, "checks": [{**checks[0], "passed": bad}, *checks[1:]]}
        with pytest.raises(LiveReadinessError):
            report_from_mapping(broken, instance_id="test-instance")
    for broken_checks in ([], checks[:-1], [*checks, checks[0]]):
        with pytest.raises(LiveReadinessError):
            report_from_mapping({**payload, "checks": broken_checks}, instance_id="test-instance")
    for field, value in (("artifact_hash", ""), ("artifact_hash", "bad"),
                         ("fingerprint", ""), ("fingerprint", "not-a-digest")):
        with pytest.raises(LiveReadinessError):
            report_from_mapping({**payload, field: value}, instance_id="test-instance")


def test_live_gate_requires_the_independent_deployed_artifact_identity():
    from live_readiness import (LiveReadinessError, ReadinessCheck, ReadinessReport,
                                ReadinessState, assert_live_start_allowed, required_live_checks)

    digest = "b" * 64
    report = ReadinessReport("test-instance", ReadinessState.LIVE_ELIGIBLE,
                             tuple(ReadinessCheck(name, True, "verified", digest)
                                   for name in required_live_checks()), digest)
    assert_live_start_allowed(report, deployed_artifact_hash=digest)
    with pytest.raises(LiveReadinessError, match="artifact"):
        assert_live_start_allowed(report, deployed_artifact_hash="c" * 64)


def test_live_gate_rejects_directly_constructed_incomplete_checks():
    from live_readiness import LiveReadinessError, ReadinessCheck, ReadinessReport, ReadinessState, assert_live_start_allowed
    report = ReadinessReport("test-instance", ReadinessState.LIVE_ELIGIBLE,
                             (ReadinessCheck("secrets", True, "verified", "a" * 64),), "b" * 64)
    with pytest.raises(LiveReadinessError, match="incomplete"):
        assert_live_start_allowed(report, deployed_artifact_hash="b" * 64)
