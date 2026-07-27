from __future__ import annotations

import json
import sys
import types
from unittest.mock import MagicMock

import pytest

from live_readiness import (
    ReadinessCheck,
    ReadinessReport,
    ReadinessState,
    required_live_checks,
)
from scripts.verify_inactive_deployment import (
    AccountInvariant,
    InactiveSnapshot,
    InactiveVerification,
    verify_inactive_deployment,
)


sys.modules.setdefault("socketio", MagicMock())
_waitress = types.ModuleType("waitress")
_waitress.serve = lambda *args, **kwargs: None
sys.modules.setdefault("waitress", _waitress)


def _report(*, artifact_hash: str, paper_passed: bool) -> ReadinessReport:
    checks = tuple(
        ReadinessCheck(
            name=name,
            passed=paper_passed if name == "paper_observation" else True,
            reason="60 exact-build sessions" if name == "paper_observation" else "passed",
            evidence_hash=(str(index + 1) * 64)[:64],
        )
        for index, name in enumerate(required_live_checks())
    )
    return ReadinessReport(
        instance_id="alpaca-main",
        state=(
            ReadinessState.LIVE_ELIGIBLE
            if paper_passed
            else ReadinessState.PAPER_ELIGIBLE
        ),
        checks=checks,
        artifact_hash=artifact_hash,
    )


def test_inactive_equities_mode_blocks_before_docker_mutation(monkeypatch):
    """Removing the launch-mode guard must advance into Docker launch work."""
    import server

    monkeypatch.setenv("EQUITIES_INSTANCE_AUTOSTART_ALLOWED", "false")
    monkeypatch.setenv("SOCKET_CONTROL_MASTER_KEY", "0123456789abcdef" * 4)
    calls = []
    preflight = types.SimpleNamespace(
        instance_id="alpaca-main",
        instance={"id": "alpaca-main", "kind": "equities"},
        client=object(),
        image_digest="a" * 64,
        image_id="sha256:" + "a" * 64,
    )
    monkeypatch.setattr(
        server,
        "_preflight_instance_launch",
        lambda *_args, **_kwargs: preflight,
    )
    monkeypatch.setattr(
        server,
        "_get_instance_network",
        lambda *_args, **_kwargs: calls.append("docker") or "network",
    )

    assert server.start_instance_container("alpaca-main") is None
    assert calls == []


def test_verifier_detects_order_and_position_delta_without_raw_account_data():
    """Dropping either invariant comparison must make this deployment pass incorrectly."""
    before = InactiveSnapshot(
        run_command=False,
        account=AccountInvariant.from_docs(
            positions=[
                {
                    "asset_id": "asset-a",
                    "symbol": "AAPL",
                    "side": "long",
                    "qty": "1",
                    "avg_entry_price": "100",
                }
            ],
            orders=[],
        ),
        account_hash="a" * 64,
        worker_state="stopped",
    )
    after = InactiveSnapshot(
        run_command=False,
        account=AccountInvariant.from_docs(
            positions=[
                {
                    "asset_id": "asset-a",
                    "symbol": "AAPL",
                    "side": "long",
                    "qty": "2",
                    "avg_entry_price": "100",
                }
            ],
            orders=[{"id": "new-order", "status": "accepted"}],
        ),
        account_hash="a" * 64,
        worker_state="stopped",
    )
    rows = iter((before, after))

    result = verify_inactive_deployment(lambda: next(rows), lambda: None)

    assert result.passed is False
    assert result.reasons == ("positions changed", "orders changed")
    assert "AAPL" not in result.evidence_hash
    assert "new-order" not in result.evidence_hash


def test_evidence_bundle_is_hashed_versioned_and_never_authorizes_unmet_paper_gate():
    """Treating engineering completion as calendar evidence must fail this test."""
    from scripts.verify_inactive_deployment import build_readiness_evidence_bundle

    artifact_hash = "a" * 64
    verification = InactiveVerification(True, (), "b" * 64)

    bundle = build_readiness_evidence_bundle(
        artifact_hash=artifact_hash,
        inactive_verification=verification,
        readiness_report=_report(
            artifact_hash=artifact_hash,
            paper_passed=False,
        ),
    )
    payload = bundle.to_mapping()

    assert payload["schema_version"] == 1
    assert payload["artifact_hash"] == artifact_hash
    assert payload["inactive_evidence_hash"] == "b" * 64
    assert payload["inactive_verified"] is True
    assert payload["activation_allowed"] is False
    assert payload["unmet_check_hashes"] == [
        __import__("hashlib").sha256(b"paper_observation").hexdigest()
    ]
    assert len(payload["readiness_fingerprint"]) == 64
    assert len(payload["bundle_hash"]) == 64
    assert "alpaca-main" not in json.dumps(payload)


@pytest.mark.parametrize("bad_hash", ["", "A" * 64, "a" * 63])
def test_evidence_bundle_rejects_noncanonical_artifact_identity(bad_hash):
    """Relaxing exact image identity must make malformed artifacts packageable."""
    from scripts.verify_inactive_deployment import build_readiness_evidence_bundle

    with pytest.raises(ValueError, match="artifact_hash"):
        build_readiness_evidence_bundle(
            artifact_hash=bad_hash,
            inactive_verification=InactiveVerification(True, (), "b" * 64),
            readiness_report=_report(
                artifact_hash="a" * 64,
                paper_passed=False,
            ),
        )
