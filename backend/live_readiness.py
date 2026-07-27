"""Immutable, fail-closed readiness contracts for real-money starts."""

from __future__ import annotations

import hashlib
import json
import re
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


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_REQUIRED_LIVE_CHECKS = (
    "secrets", "research_integrity", "execution_safety", "risk_state",
    "operations", "paper_observation",
)


def required_live_checks() -> tuple[str, ...]:
    return _REQUIRED_LIVE_CHECKS


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
    if type(payload) is not dict:
        raise LiveReadinessError("readiness report is missing")
    if type(instance_id) is not str or not instance_id or payload.get("instance_id") != instance_id:
        raise LiveReadinessError("readiness report instance does not match")
    try:
        if type(payload["state"]) is not str or type(payload["artifact_hash"]) is not str:
            raise ValueError
        if not _SHA256_RE.fullmatch(payload["artifact_hash"]):
            raise ValueError
        if type(payload["checks"]) is not list:
            raise ValueError
        checks = []
        for check in payload["checks"]:
            if type(check) is not dict:
                raise ValueError
            if (type(check.get("name")) is not str or check["name"] not in _REQUIRED_LIVE_CHECKS
                    or type(check.get("passed")) is not bool
                    or type(check.get("reason")) is not str or not check["reason"]
                    or type(check.get("evidence_hash")) is not str
                    or not _SHA256_RE.fullmatch(check["evidence_hash"])):
                raise ValueError
            checks.append(ReadinessCheck(**check))
        if len(checks) != len(_REQUIRED_LIVE_CHECKS) or {c.name for c in checks} != set(_REQUIRED_LIVE_CHECKS):
            raise ValueError
        state = ReadinessState(payload["state"])
        report = ReadinessReport(
            instance_id=instance_id,
            state=state,
            checks=tuple(checks),
            artifact_hash=payload["artifact_hash"],
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise LiveReadinessError("readiness report is malformed") from exc
    if type(payload.get("fingerprint")) is not str or not _SHA256_RE.fullmatch(payload["fingerprint"]):
        raise LiveReadinessError("readiness report fingerprint is invalid")
    if verify_fingerprint and payload["fingerprint"] != report_fingerprint(report):
        raise LiveReadinessError("readiness report fingerprint is invalid")
    return report


def assert_live_start_allowed(report: ReadinessReport, *, deployed_artifact_hash: str | None = None) -> None:
    """Permit a real-money spawn only with a complete eligible report."""
    if (type(report) is not ReadinessReport or type(report.instance_id) is not str or not report.instance_id
            or type(report.state) is not ReadinessState or type(report.checks) is not tuple
            or len(report.checks) != len(_REQUIRED_LIVE_CHECKS)
            or any(type(check) is not ReadinessCheck or type(check.name) is not str
                   or type(check.passed) is not bool or type(check.reason) is not str or not check.reason
                   or type(check.evidence_hash) is not str or not _SHA256_RE.fullmatch(check.evidence_hash)
                   for check in report.checks)
            or {check.name for check in report.checks} != set(_REQUIRED_LIVE_CHECKS)):
        raise LiveReadinessError("live readiness checks are incomplete or malformed")
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
    if (type(report.artifact_hash) is not str or not _SHA256_RE.fullmatch(report.artifact_hash)
            or type(deployed_artifact_hash) is not str
            or not _SHA256_RE.fullmatch(deployed_artifact_hash)
            or report.artifact_hash != deployed_artifact_hash):
        raise LiveReadinessError("deployed artifact identity does not match readiness report")
