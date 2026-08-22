"""Read-only verification for an inactive deployment candidate.

This module deliberately reads RethinkDB state only.  It does not import a
broker adapter or invoke an order/position operation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from decimal import Decimal, InvalidOperation
from dataclasses import dataclass
from typing import Callable, Iterable, Mapping

from db import store as _store


@dataclass(frozen=True)
class AccountInvariant:
    """Hashed account-object snapshots; raw identifiers never leave this type."""

    positions: tuple[str, ...]
    orders: tuple[str, ...]

    @classmethod
    def from_docs(cls, *, positions: Iterable[Mapping], orders: Iterable[Mapping]):
        return cls(
            positions=_position_hashes(positions),
            orders=_document_hashes(orders),
        )


@dataclass(frozen=True)
class InvariantComparison:
    passed: bool
    reasons: tuple[str, ...]
    evidence_hash: str


@dataclass(frozen=True)
class InactiveSnapshot:
    run_command: bool
    account: AccountInvariant
    account_hash: str = ""
    worker_state: str = "unknown"


@dataclass(frozen=True)
class InactiveVerification:
    passed: bool
    reasons: tuple[str, ...]
    evidence_hash: str


@dataclass(frozen=True)
class ReadinessEvidenceBundle:
    """Versioned, secret-free hashes for one inactive release candidate."""

    schema_version: int
    artifact_hash: str
    inactive_evidence_hash: str
    readiness_fingerprint: str
    inactive_verified: bool
    activation_allowed: bool
    unmet_check_hashes: tuple[str, ...]
    verification_reason_hashes: tuple[str, ...]
    bundle_hash: str

    def to_mapping(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "artifact_hash": self.artifact_hash,
            "inactive_evidence_hash": self.inactive_evidence_hash,
            "readiness_fingerprint": self.readiness_fingerprint,
            "inactive_verified": self.inactive_verified,
            "activation_allowed": self.activation_allowed,
            "unmet_check_hashes": list(self.unmet_check_hashes),
            "verification_reason_hashes": list(
                self.verification_reason_hashes
            ),
            "bundle_hash": self.bundle_hash,
        }


def build_readiness_evidence_bundle(
    *,
    artifact_hash: str,
    inactive_verification: InactiveVerification,
    readiness_report,
) -> ReadinessEvidenceBundle:
    """Bind inactive verification and readiness without exposing identifiers."""
    from live_readiness import (
        LiveReadinessError,
        ReadinessReport,
        assert_artifact_bound,
        assert_live_start_allowed,
        report_fingerprint,
    )

    if (
        type(artifact_hash) is not str
        or not re.fullmatch(r"[0-9a-f]{64}", artifact_hash)
    ):
        raise ValueError("artifact_hash must be a lowercase SHA-256 digest")
    if type(inactive_verification) is not InactiveVerification:
        raise TypeError("inactive_verification is malformed")
    if (
        type(inactive_verification.evidence_hash) is not str
        or not re.fullmatch(
            r"[0-9a-f]{64}", inactive_verification.evidence_hash
        )
    ):
        raise ValueError(
            "inactive verification evidence must be a SHA-256 digest"
        )
    if type(readiness_report) is not ReadinessReport:
        raise TypeError("readiness_report is malformed")
    try:
        assert_artifact_bound(
            readiness_report,
            deployed_artifact_hash=artifact_hash,
        )
    except LiveReadinessError as exc:
        raise ValueError(
            "artifact_hash does not match the readiness report"
        ) from exc

    inactive_verified = bool(
        inactive_verification.passed
        and not inactive_verification.reasons
    )
    activation_allowed = False
    if inactive_verified:
        try:
            assert_live_start_allowed(
                readiness_report,
                deployed_artifact_hash=artifact_hash,
            )
        except LiveReadinessError:
            pass
        else:
            activation_allowed = True

    unmet = tuple(
        sorted(
            hashlib.sha256(check.name.encode("utf-8")).hexdigest()
            for check in readiness_report.checks
            if not check.passed
        )
    )
    reason_hashes = tuple(
        sorted(
            hashlib.sha256(reason.encode("utf-8")).hexdigest()
            for reason in inactive_verification.reasons
        )
    )
    payload = {
        "schema_version": 1,
        "artifact_hash": artifact_hash,
        "inactive_evidence_hash": inactive_verification.evidence_hash,
        "readiness_fingerprint": report_fingerprint(readiness_report),
        "inactive_verified": inactive_verified,
        "activation_allowed": activation_allowed,
        "unmet_check_hashes": unmet,
        "verification_reason_hashes": reason_hashes,
    }
    bundle_hash = hashlib.sha256(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return ReadinessEvidenceBundle(
        **payload,
        bundle_hash=bundle_hash,
    )


def _number(value):
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise RuntimeError("position number is malformed")
    if not number.is_finite():
        raise RuntimeError("position number is malformed")
    return format(number.normalize(), "f")


def _position_hashes(documents: Iterable[Mapping]) -> tuple[str, ...]:
    encoded = []
    for document in documents or ():
        if not isinstance(document, Mapping):
            raise RuntimeError("broker snapshot contains malformed records")
        asset_id, symbol, side = document.get("asset_id"), document.get("symbol"), document.get("side")
        if not isinstance(symbol, str) or not symbol or not isinstance(side, str) or not side or not (isinstance(asset_id, str) and asset_id or symbol):
            raise RuntimeError("position identity is incomplete")
        document = {"asset_id": asset_id or "", "symbol": symbol, "side": side,
                    "qty": _number(document.get("qty")), "avg_entry_price": _number(document.get("avg_entry_price"))}
        normalized = json.dumps(document, sort_keys=True, separators=(",", ":"),
                                default=str)
        encoded.append(hashlib.sha256(normalized.encode("utf-8")).hexdigest())
    return tuple(sorted(encoded))


def _document_hashes(documents: Iterable[Mapping]) -> tuple[str, ...]:
    encoded = []
    for document in documents or ():
        if not isinstance(document, Mapping):
            raise RuntimeError("broker snapshot contains malformed records")
        encoded.append(hashlib.sha256(json.dumps(document, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest())
    return tuple(sorted(encoded))


def _account_identity_hash(
        instance_id: str,
        brokerage_id: str,
        brokerage: Mapping,
        authoritative_account_id: str,
) -> str:
    """Bind the snapshot to the exact stock account link and environment."""
    if (type(instance_id) is not str or not instance_id
            or type(brokerage_id) is not str or not brokerage_id
            or type(brokerage) is not dict
            or brokerage.get("id") != brokerage_id
            or brokerage.get("brokerage_type") != "alpaca"
            or brokerage.get("alpaca_paper") is not False
            or brokerage.get("alpaca_base_url")
            not in {"https://api.alpaca.markets",
                    "https://api.alpaca.markets/"}
            or type(authoritative_account_id) is not str
            or not authoritative_account_id
            or brokerage.get("alpaca_account_number")
            != authoritative_account_id):
        raise RuntimeError("linked stock account identity is malformed")
    identity = {
        "instance_id": instance_id,
        "brokerage_id": brokerage_id,
        "brokerage_type": brokerage["brokerage_type"],
        "paper": brokerage["alpaca_paper"],
        "endpoint": brokerage["alpaca_base_url"],
        "linked_account": brokerage["alpaca_account_number"],
        "authoritative_account": authoritative_account_id,
    }
    canonical = json.dumps(
        identity, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def compare_account_invariants(before: AccountInvariant,
                               after: AccountInvariant) -> InvariantComparison:
    reasons = []
    if before.positions != after.positions:
        reasons.append("positions changed")
    if before.orders != after.orders:
        reasons.append("orders changed")
    evidence = hashlib.sha256(
        repr((before.positions, before.orders, after.positions, after.orders)).encode("utf-8")
    ).hexdigest()
    return InvariantComparison(not reasons, tuple(reasons), evidence)


def verify_inactive_deployment(read_snapshot: Callable[[], InactiveSnapshot],
                               validate_artifact: Callable[[], None]) -> InactiveVerification:
    """Read before and after validating artifacts; fail on any observed change."""
    before = read_snapshot()
    reasons = []
    if type(before.run_command) is not bool or before.run_command:
        reasons.append("runCommand is enabled before validation")
    if before.worker_state != "stopped" or not before.account_hash:
        reasons.append("instance worker state is not proven stopped")
    try:
        validate_artifact()
    except Exception:
        reasons.append("artifact validation failed")
    after = read_snapshot()
    if type(after.run_command) is not bool or after.run_command:
        reasons.append("runCommand is enabled after validation")
    if after.worker_state != "stopped" or after.account_hash != before.account_hash:
        reasons.append("instance worker or linked account changed")
    comparison = compare_account_invariants(before.account, after.account)
    reasons.extend(comparison.reasons)
    evidence = hashlib.sha256(
        repr((before.run_command, after.run_command, comparison.evidence_hash)).encode("utf-8")
    ).hexdigest()
    return InactiveVerification(not reasons, tuple(reasons), evidence)


def _snapshot_reader(conn, instance_id: str, broker_snapshot_reader, worker_state_reader, expected_artifact_hash) -> InactiveSnapshot:
    r = _store
    instance = r.get("Instances", instance_id)
    if not isinstance(instance, Mapping):
        raise RuntimeError("instance state is unavailable")
    if type(instance.get("runCommand")) is not bool:
        raise RuntimeError("runCommand is not a boolean")
    from live_readiness import assert_artifact_bound, report_from_mapping
    report = report_from_mapping(instance.get("live_readiness_report"), instance_id=instance_id)
    assert_artifact_bound(
        report, deployed_artifact_hash=expected_artifact_hash)
    brokerage_id = instance.get("brokerage_id")
    if not isinstance(brokerage_id, str) or not brokerage_id:
        raise RuntimeError("linked brokerage is unavailable")
    brokerage = r.get("BrokerageAccounts", brokerage_id)
    if not isinstance(brokerage, Mapping):
        raise RuntimeError("linked brokerage is unavailable")
    state = broker_snapshot_reader(instance, brokerage)
    if not isinstance(state, Mapping):
        raise RuntimeError("authoritative broker snapshot is unavailable")
    account_id = state.get("account_id")
    if not isinstance(account_id, str) or not account_id:
        raise RuntimeError("authoritative broker account is unavailable")
    linked_account_id = brokerage.get("account_id")
    if isinstance(linked_account_id, str) and linked_account_id and linked_account_id != account_id:
        raise RuntimeError("authoritative broker account does not match linked account")
    positions, open_orders, recent_orders, recent_trades = (state.get("positions"), state.get("open_orders"),
                                                              state.get("recent_orders"), state.get("recent_trades"))
    if not all(isinstance(rows, list) for rows in (positions, open_orders, recent_orders, recent_trades)):
        raise RuntimeError("authoritative broker snapshot is incomplete")
    return InactiveSnapshot(
        run_command=instance["runCommand"],
        account=AccountInvariant.from_docs(
            positions=positions,
            orders=[*open_orders, *recent_orders, *recent_trades],
        ),
        account_hash=_account_identity_hash(
            instance_id, brokerage_id, brokerage, account_id),
        worker_state=worker_state_reader(instance_id),
    )


def _validate_deployed_image(image_ref: str, expected_hash: str, *, client=None) -> None:
    if type(image_ref) is not str or not image_ref or type(expected_hash) is not str or not re.fullmatch(r"[0-9a-f]{64}", expected_hash):
        raise RuntimeError("deployed image identity is unavailable")
    if client is None:
        import docker
        client = docker.from_env()
    image = client.images.get(image_ref)
    if getattr(image, "id", "") != "sha256:" + expected_hash:
        raise RuntimeError("deployed image identity is invalid")


def _docker_worker_state(instance_id: str, *, client=None) -> str:
    try:
        if client is None:
            import docker
            client = docker.from_env()
        name = "intellistock-instance-" + "".join(c if c.isalnum() or c in "_.-" else "_" for c in instance_id)[:50]
        container = client.containers.get(name)
        container.reload()
        state = getattr(container, "attrs", {}).get("State", {})
        restart_policy = getattr(container, "attrs", {}).get("HostConfig", {}).get("RestartPolicy", {}).get("Name", "")
        if state.get("Running") is False and state.get("Paused") is False and state.get("Restarting") is False and not state.get("Restarting") and restart_policy in {"", "no", "none"} and state.get("Status") in {"exited", "dead"}:
            return "stopped"
        return "running" if state.get("Running") is True else "unknown"
    except Exception as exc:
        if exc.__class__.__name__.lower().endswith("notfound"):
            return "stopped"
        return "unknown"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Read-only inactive deployment verifier")
    parser.add_argument("--instance-id", required=True)
    parser.add_argument("--image-ref", default=os.environ.get("DOCKER_INSTANCE_IMAGE", ""))
    parser.add_argument("--host", default=os.environ.get("RETHINKDB_HOST", "localhost"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("RETHINKDB_PORT", "28015")))
    args = parser.parse_args(argv)
    expected_hash = os.environ.get("INTELLISTOCK_DEPLOYED_ARTIFACT_SHA256", "")
    if len(expected_hash) != 64:
        print(json.dumps({"passed": False, "reasons": ["deployed artifact identity is unavailable"]}))
        return 1
    from read_only_broker_snapshot import read_authoritative_snapshot
    r, conn = _store, None
    try:
        result = verify_inactive_deployment(
            lambda: _snapshot_reader(conn, args.instance_id, read_authoritative_snapshot, _docker_worker_state, expected_hash),
            lambda: _validate_deployed_image(args.image_ref, expected_hash),
        )
    finally:
        conn.close()
    print(json.dumps({"passed": result.passed, "reasons": result.reasons,
                      "evidence_hash": result.evidence_hash}, sort_keys=True))
    return 0 if result.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
