"""Read-only verification for an inactive deployment candidate.

This module deliberately reads RethinkDB state only.  It does not import a
broker adapter or invoke an order/position operation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
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
            positions=_document_hashes(positions),
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


@dataclass(frozen=True)
class InactiveVerification:
    passed: bool
    reasons: tuple[str, ...]
    evidence_hash: str


def _document_hashes(documents: Iterable[Mapping]) -> tuple[str, ...]:
    encoded = []
    for document in documents or ():
        normalized = json.dumps(document, sort_keys=True, separators=(",", ":"),
                                default=str)
        encoded.append(hashlib.sha256(normalized.encode("utf-8")).hexdigest())
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
    if before.run_command:
        reasons.append("runCommand is enabled before validation")
    try:
        validate_artifact()
    except Exception:
        reasons.append("artifact validation failed")
    after = read_snapshot()
    if after.run_command:
        reasons.append("runCommand is enabled after validation")
    comparison = compare_account_invariants(before.account, after.account)
    reasons.extend(comparison.reasons)
    evidence = hashlib.sha256(
        repr((before.run_command, after.run_command, comparison.evidence_hash)).encode("utf-8")
    ).hexdigest()
    return InactiveVerification(not reasons, tuple(reasons), evidence)


def _snapshot_reader(conn, instance_id: str) -> InactiveSnapshot:
    r = RethinkDB()
    instance = r.db("IntelliStock").table("Instances").get(instance_id).run(conn)
    if not isinstance(instance, Mapping):
        raise RuntimeError("instance state is unavailable")
    state = r.db("IntelliStock").table("LiveState").get(instance_id).run(conn)
    if not isinstance(state, Mapping):
        raise RuntimeError("live state is unavailable")
    return InactiveSnapshot(
        run_command=instance.get("runCommand") is True,
        account=AccountInvariant.from_docs(
            positions=state.get("positions") or (),
            orders=state.get("orders") or state.get("recent_orders") or (),
        ),
    )


def _validate_artifact(path: str) -> None:
    artifact = Path(path)
    if not artifact.is_file():
        raise RuntimeError("artifact is unavailable")
    with artifact.open("rb") as stream:
        while stream.read(1024 * 1024):
            pass


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Read-only inactive deployment verifier")
    parser.add_argument("--instance-id", required=True)
    parser.add_argument("--artifact", required=True)
    parser.add_argument("--host", default=os.environ.get("RETHINKDB_HOST", "localhost"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("RETHINKDB_PORT", "28015")))
    args = parser.parse_args(argv)
    r = RethinkDB()
    conn = r.connect(host=args.host, port=args.port)
    try:
        result = verify_inactive_deployment(
            lambda: _snapshot_reader(conn, args.instance_id),
            lambda: _validate_artifact(args.artifact),
        )
    finally:
        conn.close()
    print(json.dumps({"passed": result.passed, "reasons": result.reasons,
                      "evidence_hash": result.evidence_hash}, sort_keys=True))
    return 0 if result.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
