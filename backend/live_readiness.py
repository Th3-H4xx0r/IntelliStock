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


_STATE_ORDER = (
    ReadinessState.RESEARCH,
    ReadinessState.PAPER_ELIGIBLE,
    ReadinessState.CANARY_ELIGIBLE,
    ReadinessState.LIVE_ELIGIBLE,
    ReadinessState.LIVE_RUNNING,
)


def assert_readiness_transition_allowed(
    current: ReadinessState,
    target: ReadinessState,
    *,
    evidence_state: ReadinessState,
    explicit_activation: bool = False,
) -> None:
    """Enforce adjacent, evidence-bounded promotion and explicit activation.

    Demotion is always allowed.  Promotion can advance by one state only and
    can never exceed the state proven by immutable evidence. ``LIVE_RUNNING``
    additionally requires a distinct explicit activation action.
    """

    if not all(
        isinstance(value, ReadinessState)
        for value in (current, target, evidence_state)
    ):
        raise LiveReadinessError("readiness transition values are malformed")
    current_rank = _STATE_ORDER.index(current)
    target_rank = _STATE_ORDER.index(target)
    evidence_rank = _STATE_ORDER.index(evidence_state)
    if target_rank <= current_rank:
        return
    if target_rank != current_rank + 1:
        raise LiveReadinessError("readiness promotion cannot skip states")
    if target_rank > evidence_rank:
        raise LiveReadinessError("readiness evidence does not authorize target")
    if (
        target is ReadinessState.LIVE_RUNNING
        and explicit_activation is not True
    ):
        raise LiveReadinessError(
            "LIVE_RUNNING requires a separate explicit activation"
        )


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


def _assert_report_well_formed(report: ReadinessReport) -> None:
    if (type(report) is not ReadinessReport or type(report.instance_id) is not str or not report.instance_id
            or type(report.state) is not ReadinessState or type(report.checks) is not tuple
            or len(report.checks) != len(_REQUIRED_LIVE_CHECKS)
            or any(type(check) is not ReadinessCheck or type(check.name) is not str
                   or type(check.passed) is not bool or type(check.reason) is not str or not check.reason
                   or type(check.evidence_hash) is not str or not _SHA256_RE.fullmatch(check.evidence_hash)
                   for check in report.checks)
            or {check.name for check in report.checks} != set(_REQUIRED_LIVE_CHECKS)):
        raise LiveReadinessError("live readiness checks are incomplete or malformed")


def assert_artifact_bound(
        report: ReadinessReport,
        *,
        deployed_artifact_hash: str | None = None,
) -> None:
    """Validate report structure and exact deployed artifact identity.

    This deliberately does not grant funded-trading permission.  It exists for
    stopped-image verification before paper observation is complete.
    """
    _assert_report_well_formed(report)
    if (type(report.artifact_hash) is not str
            or not _SHA256_RE.fullmatch(report.artifact_hash)
            or type(deployed_artifact_hash) is not str
            or not _SHA256_RE.fullmatch(deployed_artifact_hash)
            or report.artifact_hash != deployed_artifact_hash):
        raise LiveReadinessError(
            "deployed artifact identity does not match readiness report")


def assert_live_start_allowed(report: ReadinessReport, *, deployed_artifact_hash: str | None = None) -> None:
    """Permit a real-money spawn only with a complete eligible report."""
    assert_artifact_bound(
        report, deployed_artifact_hash=deployed_artifact_hash)
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


def brokerage_requires_live_gate(
        instance: Mapping,
        brokerage: Mapping,
        *,
        environ: Mapping | None = None,
) -> bool:
    """Classify a stock broker start as paper or funded, fail-closed.

    Alpaca uses its persisted paper flag. Unsupported stock brokers fail closed.
    """
    if type(instance) is not dict:
        raise LiveReadinessError("instance configuration is unavailable")
    kind = instance.get("kind")
    # 2026-08-03 — `crypto` belongs here. The crypto platform is the SAME
    # codebase reached through the same broker.py and the same Alpaca adapter,
    # gated on kind rather than forked, so its paper/funded classification is
    # decided by exactly the same `alpaca_paper` flag as equities. The check
    # below is therefore already correct for it.
    #
    # It was excluded only because crypto could never reach this call: the
    # supervisor's launch dispatch had no "crypto" branch and refused to start
    # such an instance at all (fixed 435e508). With launching fixed, the
    # long-running crypto soak reached this guard for the first time and
    # crash-looped on `instance is not an equities configuration` — a live
    # instance that had been running since 2026-07-27 taken down by a guard it
    # had simply never been routed through before.
    #
    # Unsupported brokerage types still fail CLOSED at the bottom of this
    # function, so a Binance.US crypto account is treated as funded until it is
    # explicitly classified. That is the safe direction and is unchanged.
    if kind not in (None, "", "equities", "crypto"):
        raise LiveReadinessError("instance kind is not a tradable configuration")
    brokerage_id = instance.get("brokerage_id")
    if type(brokerage_id) is not str or not brokerage_id:
        raise LiveReadinessError("linked brokerage is unavailable")
    if (type(brokerage) is not dict
            or brokerage.get("id") != brokerage_id):
        raise LiveReadinessError("linked brokerage does not match instance")

    brokerage_type = brokerage.get("brokerage_type")
    if brokerage_type == "alpaca":
        paper = brokerage.get("alpaca_paper")
        if type(paper) is not bool:
            raise LiveReadinessError("Alpaca execution mode is malformed")
        return paper is False
    raise LiveReadinessError("stock brokerage type is unsupported")
