"""Immutable statistical and operational promotion decisions.

Evaluation is pure and returns every failed gate.  Recording a successful
decision is a separate, explicit operator action; this module cannot start an
instance or move a readiness state to ``LIVE_RUNNING``.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import ClassVar

from live_readiness import (
    ReadinessCheck,
    ReadinessReport,
    ReadinessState,
)


_DIGEST = re.compile(r"^[0-9a-f]{64}$")
MAX_WATCHDOG_AGE_SECONDS = 60.0
MIN_PAPER_TRADING_DAYS = 60


class PromotionRecordError(RuntimeError):
    """A promotion record is not eligible for append-only persistence."""


def _canonical(value) -> str:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), default=str
    )


def _hash(value) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class PromotionEvidence:
    instance_id: str
    artifact_hash: str
    deployed_artifact_hash: str
    paper_artifact_hash: str
    config_hash: str
    observed_config_hash: str
    paper_config_hash: str
    model_hash: str
    observed_model_hash: str
    data_manifest_hash: str
    observed_data_manifest_hash: str
    cost_model_hash: str
    risk_policy_hash: str
    broker_adapter_hash: str
    point_in_time_months: int
    unseen_months: int
    regime_count: int
    purged_fold_count: int
    sealed_holdout_preregistered: bool
    sealed_holdout_evaluations: int
    aligned_spy_total_return: bool
    costed_fills: bool
    registered_trial_count: int
    repeats_predeclared: bool
    median_repeat_used: bool
    median_annual_active_pp: float
    target_annual_active_pp: float
    bootstrap_active_low: float
    information_ratio: float
    deflated_sharpe_probability: float
    max_drawdown_magnitude: float
    beta: float
    profit_factor_after_costs: float
    positive_unseen_quarter_fraction: float
    parameter_stability_passed: bool
    leave_one_winner_out_passed: bool
    concentration_analysis_passed: bool
    ci_passed: bool
    lifecycle_chaos_passed: bool
    dependency_chaos_passed: bool
    restart_state_passed: bool
    secret_migration_rehearsed: bool
    rollback_rehearsed: bool
    plaintext_credentials_found: int
    unresolved_high_critical_findings: int
    unresolved_ownership: int
    watchdog_healthy: bool
    watchdog_age_seconds: float
    degraded_audit_intervals: int
    exact_build_paper: bool
    paper_trading_days: int
    point_in_time_provenance_verified: bool = False

    def __post_init__(self) -> None:
        instance = str(self.instance_id or "").strip()
        if not instance:
            raise ValueError("instance_id is required")
        object.__setattr__(self, "instance_id", instance)
        for name in (
            "artifact_hash",
            "deployed_artifact_hash",
            "paper_artifact_hash",
            "config_hash",
            "observed_config_hash",
            "paper_config_hash",
            "model_hash",
            "observed_model_hash",
            "data_manifest_hash",
            "observed_data_manifest_hash",
            "cost_model_hash",
            "risk_policy_hash",
            "broker_adapter_hash",
        ):
            value = str(getattr(self, name) or "").strip().lower()
            if not _DIGEST.fullmatch(value):
                raise ValueError(f"{name} must be a SHA-256 digest")
            object.__setattr__(self, name, value)
        for name in (
            "point_in_time_months",
            "unseen_months",
            "regime_count",
            "purged_fold_count",
            "sealed_holdout_evaluations",
            "registered_trial_count",
            "plaintext_credentials_found",
            "unresolved_high_critical_findings",
            "unresolved_ownership",
            "degraded_audit_intervals",
            "paper_trading_days",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or int(value) != value or int(value) < 0:
                raise ValueError(f"{name} must be a non-negative integer")
            object.__setattr__(self, name, int(value))
        for name in (
            "median_annual_active_pp",
            "target_annual_active_pp",
            "bootstrap_active_low",
            "information_ratio",
            "deflated_sharpe_probability",
            "max_drawdown_magnitude",
            "beta",
            "profit_factor_after_costs",
            "positive_unseen_quarter_fraction",
            "watchdog_age_seconds",
        ):
            value = float(getattr(self, name))
            if not math.isfinite(value):
                raise ValueError(f"{name} must be finite")
            object.__setattr__(self, name, value)
        if self.max_drawdown_magnitude < 0:
            raise ValueError("max_drawdown_magnitude cannot be negative")
        for name in (
            "deflated_sharpe_probability",
            "positive_unseen_quarter_fraction",
        ):
            if not 0 <= getattr(self, name) <= 1:
                raise ValueError(f"{name} must be in [0, 1]")
        if self.watchdog_age_seconds < 0:
            raise ValueError("watchdog_age_seconds cannot be negative")

    @property
    def evidence_hash(self) -> str:
        return _hash(asdict(self))


@dataclass(frozen=True, slots=True)
class PromotionDecision:
    passed: bool
    reasons: tuple[str, ...]
    eligible_state: ReadinessState
    evidence_hash: str
    decision_hash: str


_RESEARCH_REASONS = frozenset(
    {
        "history_months",
        "unseen_months",
        "regime_count",
        "purged_folds",
        "sealed_holdout",
        "benchmark_alignment",
        "costed_fills",
        "trial_count",
        "predeclared_repeats",
        "median_repeat",
        "median_active",
        "target_active",
        "bootstrap",
        "information_ratio",
        "deflated_sharpe",
        "max_drawdown",
        "beta",
        "profit_factor",
        "unseen_quarters",
        "parameter_stability",
        "leave_one_winner_out",
        "concentration_analysis",
        "artifact_mismatch",
        "config_mismatch",
        "model_mismatch",
        "data_mismatch",
        "point_in_time_provenance",
    }
)
_PAPER_REASONS = frozenset({"paper_build", "paper_config", "paper_days"})


def evaluate_promotion(evidence: PromotionEvidence) -> PromotionDecision:
    if not isinstance(evidence, PromotionEvidence):
        raise TypeError("evidence must be PromotionEvidence")
    reasons: list[str] = []

    def require(condition, reason):
        if not condition:
            reasons.append(reason)

    require(evidence.point_in_time_months >= 24, "history_months")
    require(
        evidence.point_in_time_provenance_verified is True,
        "point_in_time_provenance",
    )
    require(evidence.unseen_months >= 12, "unseen_months")
    require(evidence.regime_count >= 3, "regime_count")
    require(evidence.purged_fold_count >= 1, "purged_folds")
    require(
        evidence.sealed_holdout_preregistered
        and evidence.sealed_holdout_evaluations == 1,
        "sealed_holdout",
    )
    require(evidence.aligned_spy_total_return, "benchmark_alignment")
    require(evidence.costed_fills, "costed_fills")
    require(evidence.registered_trial_count >= 1, "trial_count")
    require(evidence.repeats_predeclared, "predeclared_repeats")
    require(evidence.median_repeat_used, "median_repeat")
    require(evidence.median_annual_active_pp >= 8.0, "median_active")
    require(evidence.target_annual_active_pp >= 10.0, "target_active")
    require(evidence.bootstrap_active_low > 0.0, "bootstrap")
    require(evidence.information_ratio >= 0.75, "information_ratio")
    require(
        evidence.deflated_sharpe_probability >= 0.95,
        "deflated_sharpe",
    )
    require(evidence.max_drawdown_magnitude <= 0.15, "max_drawdown")
    require(0.8 <= evidence.beta <= 1.1, "beta")
    require(evidence.profit_factor_after_costs > 1.0, "profit_factor")
    require(
        evidence.positive_unseen_quarter_fraction >= 0.60,
        "unseen_quarters",
    )
    require(evidence.parameter_stability_passed, "parameter_stability")
    require(evidence.leave_one_winner_out_passed, "leave_one_winner_out")
    require(
        evidence.concentration_analysis_passed, "concentration_analysis"
    )

    require(
        evidence.artifact_hash == evidence.deployed_artifact_hash,
        "artifact_mismatch",
    )
    require(
        evidence.config_hash == evidence.observed_config_hash,
        "config_mismatch",
    )
    require(
        evidence.model_hash == evidence.observed_model_hash,
        "model_mismatch",
    )
    require(
        evidence.data_manifest_hash
        == evidence.observed_data_manifest_hash,
        "data_mismatch",
    )
    require(evidence.ci_passed, "ci")
    require(evidence.lifecycle_chaos_passed, "lifecycle_chaos")
    require(evidence.dependency_chaos_passed, "dependency_chaos")
    require(evidence.restart_state_passed, "restart_state")
    require(evidence.secret_migration_rehearsed, "secret_migration")
    require(evidence.rollback_rehearsed, "rollback")
    require(
        evidence.plaintext_credentials_found == 0,
        "plaintext_credentials",
    )
    require(
        evidence.unresolved_high_critical_findings == 0,
        "unresolved_findings",
    )
    require(evidence.unresolved_ownership == 0, "ownership")
    require(
        evidence.watchdog_healthy
        and evidence.watchdog_age_seconds <= MAX_WATCHDOG_AGE_SECONDS,
        "watchdog",
    )
    require(evidence.degraded_audit_intervals == 0, "degraded_audit")
    require(
        evidence.exact_build_paper
        and evidence.paper_artifact_hash == evidence.artifact_hash,
        "paper_build",
    )
    require(
        evidence.paper_config_hash == evidence.config_hash,
        "paper_config",
    )
    require(
        evidence.paper_trading_days >= MIN_PAPER_TRADING_DAYS,
        "paper_days",
    )

    research_failed = any(reason in _RESEARCH_REASONS for reason in reasons)
    non_paper_failed = any(
        reason not in _PAPER_REASONS for reason in reasons
    )
    if research_failed:
        state = ReadinessState.RESEARCH
    elif non_paper_failed or evidence.paper_trading_days == 0:
        state = ReadinessState.PAPER_ELIGIBLE
    elif reasons:
        state = ReadinessState.CANARY_ELIGIBLE
    else:
        state = ReadinessState.LIVE_ELIGIBLE
    evidence_hash = evidence.evidence_hash
    decision_payload = {
        "passed": not reasons,
        "reasons": reasons,
        "eligible_state": state.value,
        "evidence_hash": evidence_hash,
    }
    return PromotionDecision(
        passed=not reasons,
        reasons=tuple(reasons),
        eligible_state=state,
        evidence_hash=evidence_hash,
        decision_hash=_hash(decision_payload),
    )


def _check(name, reasons, relevant, evidence_hash):
    failures = tuple(reason for reason in reasons if reason in relevant)
    passed = not failures
    reason = "verified" if passed else ",".join(failures)
    return ReadinessCheck(
        name=name,
        passed=passed,
        reason=reason,
        evidence_hash=_hash(
            {
                "name": name,
                "passed": passed,
                "reason": reason,
                "promotion_evidence": evidence_hash,
            }
        ),
    )


def readiness_report_from_promotion(
    evidence: PromotionEvidence,
    decision: PromotionDecision | None = None,
) -> ReadinessReport:
    decision = decision or evaluate_promotion(evidence)
    if decision.evidence_hash != evidence.evidence_hash:
        raise ValueError("promotion decision does not match evidence")
    reasons = decision.reasons
    secrets = {"secret_migration", "rollback", "plaintext_credentials"}
    execution = {
        "lifecycle_chaos",
        "dependency_chaos",
        "ownership",
        "degraded_audit",
    }
    risk = {"restart_state"}
    operations = {"ci", "unresolved_findings", "watchdog"}
    paper = _PAPER_REASONS
    research = _RESEARCH_REASONS
    checks = (
        _check("secrets", reasons, secrets, decision.evidence_hash),
        _check(
            "research_integrity",
            reasons,
            research,
            decision.evidence_hash,
        ),
        _check(
            "execution_safety",
            reasons,
            execution,
            decision.evidence_hash,
        ),
        _check("risk_state", reasons, risk, decision.evidence_hash),
        _check("operations", reasons, operations, decision.evidence_hash),
        _check(
            "paper_observation", reasons, paper, decision.evidence_hash
        ),
    )
    return ReadinessReport(
        instance_id=evidence.instance_id,
        state=decision.eligible_state,
        checks=checks,
        artifact_hash=evidence.artifact_hash,
    )


@dataclass(frozen=True, slots=True)
class PromotionRecord:
    TABLE: ClassVar[str] = "AlphaPromotions"

    record_id: str
    instance_id: str
    state: str
    artifact_hash: str
    evidence_hash: str
    decision_hash: str
    operator_id_hash: str
    approved_at: str

    def to_doc(self) -> dict:
        return {
            "id": self.record_id,
            "record_kind": "promotion",
            "instance_id": self.instance_id,
            "state": self.state,
            "artifact_hash": self.artifact_hash,
            "evidence_hash": self.evidence_hash,
            "decision_hash": self.decision_hash,
            "operator_id_hash": self.operator_id_hash,
            "approved_at": self.approved_at,
        }


class InMemoryPromotionStore:
    def __init__(self):
        self._rows: dict[str, dict] = {}

    def insert_promotion(self, record: PromotionRecord) -> bool:
        doc = record.to_doc()
        prior = self._rows.get(record.record_id)
        if prior is None:
            self._rows[record.record_id] = doc
            return True
        if prior == doc:
            return False
        raise PromotionRecordError("divergent immutable promotion record")

    def get(self, record_id: str):
        row = self._rows.get(str(record_id))
        return dict(row) if row is not None else None


class RethinkPromotionStore:
    """Append-only adapter over ``AlphaRethinkStore.write_record``."""

    def __init__(self, alpha_store):
        self._store = alpha_store

    def insert_promotion(self, record: PromotionRecord) -> bool:
        return self._store.write_record(record)


def record_promotion(
    store,
    evidence: PromotionEvidence,
    decision: PromotionDecision,
    *,
    operator_id: str,
    approved: bool,
    created_at: datetime,
) -> PromotionRecord:
    operator = str(operator_id or "").strip()
    if not operator or approved is not True:
        raise PromotionRecordError(
            "promotion requires explicit authenticated operator approval"
        )
    if not decision.passed or (
        decision.eligible_state is not ReadinessState.LIVE_ELIGIBLE
    ):
        raise PromotionRecordError("failed evidence cannot be promoted")
    if decision.evidence_hash != evidence.evidence_hash:
        raise PromotionRecordError("decision/evidence identity mismatch")
    if not isinstance(created_at, datetime) or created_at.tzinfo is None:
        raise PromotionRecordError("created_at must be timezone-aware")
    approved_at = created_at.astimezone(timezone.utc).isoformat()
    operator_hash = hashlib.sha256(operator.encode("utf-8")).hexdigest()
    identity = {
        "instance_id": evidence.instance_id,
        "state": ReadinessState.LIVE_ELIGIBLE.value,
        "artifact_hash": evidence.artifact_hash,
        "evidence_hash": decision.evidence_hash,
        "decision_hash": decision.decision_hash,
        "operator_id_hash": operator_hash,
        "approved_at": approved_at,
    }
    record = PromotionRecord(
        record_id="promotion-" + _hash(identity),
        operator_id_hash=operator_hash,
        approved_at=approved_at,
        **{
            key: value
            for key, value in identity.items()
            if key not in {"operator_id_hash", "approved_at"}
        },
    )
    try:
        store.insert_promotion(record)
    except PromotionRecordError:
        raise
    except Exception as exc:
        raise PromotionRecordError(
            f"promotion persistence failed: {type(exc).__name__}"
        ) from exc
    return record
