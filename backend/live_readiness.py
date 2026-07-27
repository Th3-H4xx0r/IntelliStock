"""Immutable, fail-closed readiness contracts for real-money starts."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Mapping


class LiveReadinessError(RuntimeError):
    """A live broker cannot start because its eligibility is not proven."""


class ReadinessState(str, Enum):
    RESEARCH = "RESEARCH"
    PAPER_ELIGIBLE = "PAPER_ELIGIBLE"
    CANARY_ELIGIBLE = "CANARY_ELIGIBLE"
    LIVE_ELIGIBLE = "LIVE_ELIGIBLE"
    LIVE_RUNNING = "LIVE_RUNNING"


@dataclass(frozen=True)
class ReadinessCheck:
    name: str
    passed: bool
    reason: str
    evidence_hash: str


@dataclass(frozen=True)
class ReadinessReport:
    instance_id: str
    state: ReadinessState
    checks: tuple[ReadinessCheck, ...]
    artifact_hash: str


def _canonical_payload(report: ReadinessReport) -> str:
    return json.dumps(asdict(report), sort_keys=True, separators=(",", ":"))


def report_fingerprint(report: ReadinessReport) -> str:
    """Return a stable fingerprint without exposing readiness evidence itself."""
    return hashlib.sha256(_canonical_payload(report).encode("utf-8")).hexdigest()


def report_from_mapping(payload: Mapping, *, instance_id: str,
                        verify_fingerprint: bool = True) -> ReadinessReport:
    """Parse a persisted report and, by default, verify its fingerprint."""
    if not isinstance(payload, Mapping):
        raise LiveReadinessError("readiness report is missing")
    if payload.get("instance_id") != instance_id:
        raise LiveReadinessError("readiness report instance does not match")
    try:
        state = ReadinessState(str(payload["state"]))
        checks = tuple(
            ReadinessCheck(
                name=str(check["name"]),
                passed=bool(check["passed"]),
                reason=str(check["reason"]),
                evidence_hash=str(check["evidence_hash"]),
            )
            for check in payload["checks"]
        )
        report = ReadinessReport(
            instance_id=instance_id,
            state=state,
            checks=checks,
            artifact_hash=str(payload["artifact_hash"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise LiveReadinessError("readiness report is malformed") from exc
    if verify_fingerprint and payload.get("fingerprint") != report_fingerprint(report):
        raise LiveReadinessError("readiness report fingerprint is invalid")
    return report


def assert_live_start_allowed(report: ReadinessReport) -> None:
    """Permit a real-money spawn only with a complete eligible report."""
    failures = [check.name for check in report.checks if not check.passed]
    if failures:
        raise LiveReadinessError(
            "live readiness checks unmet: " + ", ".join(sorted(failures))
        )
    if report.state not in {ReadinessState.LIVE_ELIGIBLE,
                            ReadinessState.LIVE_RUNNING}:
        raise LiveReadinessError(
            f"live readiness state is {report.state.value}, not LIVE_ELIGIBLE"
        )
