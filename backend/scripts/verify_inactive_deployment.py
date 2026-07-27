"""Read-only verification for an inactive deployment candidate.

This module deliberately reads RethinkDB state only.  It does not import a
broker adapter or invoke an order/position operation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from decimal import Decimal, InvalidOperation
from dataclasses import dataclass
from typing import Callable, Iterable, Mapping

from rethinkdb import RethinkDB


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
    r = RethinkDB()
    instance = r.db("IntelliStock").table("Instances").get(instance_id).run(conn)
    if not isinstance(instance, Mapping):
        raise RuntimeError("instance state is unavailable")
    if type(instance.get("runCommand")) is not bool:
        raise RuntimeError("runCommand is not a boolean")
    from live_readiness import assert_live_start_allowed, report_from_mapping
    report = report_from_mapping(instance.get("live_readiness_report"), instance_id=instance_id)
    assert_live_start_allowed(report, deployed_artifact_hash=expected_artifact_hash)
    brokerage_id = instance.get("brokerage_id")
    if not isinstance(brokerage_id, str) or not brokerage_id:
        raise RuntimeError("linked brokerage is unavailable")
    brokerage = r.db("IntelliStock").table("BrokerageAccounts").get(brokerage_id).run(conn)
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
        account_hash=hashlib.sha256(account_id.encode("utf-8")).hexdigest(),
        worker_state=worker_state_reader(instance_id),
    )


def _validate_deployed_image(image_ref: str, expected_hash: str, *, client=None) -> None:
    import re
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
    r = RethinkDB()
    conn = r.connect(host=args.host, port=args.port)
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
