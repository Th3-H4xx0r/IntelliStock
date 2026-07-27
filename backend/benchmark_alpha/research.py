"""Registered purged walk-forward research harness + Stage B gate (Task 16).

Every experiment is registered BEFORE execution and stopped/failed runs stay
visible — the Deflated Sharpe trial count uses all of them. Data enters only
through a content-hashed point-in-time manifest (unprovable ``known_at`` is
refused). The sealed holdout evaluates once per model family. The Stage B
gate consumes fresh-direct LIVE_40 research-policy results ONLY and, on
pass, grants permission to BUILD Tasks 12-15 — never to trade.
"""
import hashlib
import json
from dataclasses import dataclass, field
from datetime import date, timedelta
from collections.abc import Callable, Mapping

from experiment_registry import ExperimentRegistry as ImmutableExperimentRegistry
from experiment_registry import ExperimentSpec as ImmutableExperimentSpec


@dataclass(frozen=True)
class ExperimentSpec:
    model_family: str
    horizons: tuple
    active_ceiling: float
    train_months: int
    calibration_months: int
    test_months: int
    embargo_days: int
    data_snapshot_id: str
    code_hash: str
    model_hash: str
    cost_model_version: str

    @property
    def experiment_id(self):
        canonical = json.dumps({
            "model_family": self.model_family,
            "horizons": list(self.horizons),
            "active_ceiling": self.active_ceiling,
            "train_months": self.train_months,
            "calibration_months": self.calibration_months,
            "test_months": self.test_months,
            "embargo_days": self.embargo_days,
            "data_snapshot_id": self.data_snapshot_id,
            "code_hash": self.code_hash,
            "model_hash": self.model_hash,
            "cost_model_version": self.cost_model_version,
        }, sort_keys=True, separators=(",", ":"))
        return "exp-" + hashlib.sha256(canonical.encode()).hexdigest()[:24]


@dataclass(frozen=True)
class WalkForwardSplit:
    train_dates: tuple
    calibration_dates: tuple
    test_dates: tuple


def _month_key(iso_date):
    return iso_date[:7]


def walk_forward_splits(dates, train_months=12, calibration_months=3,
                        test_months=3, embargo_days=5):
    """Ordered, non-overlapping purged splits over calendar months with an
    embargo gap between calibration end and test start."""
    dates = sorted(str(d) for d in dates or ())
    months = sorted({_month_key(d) for d in dates})
    by_month = {m: [d for d in dates if _month_key(d) == m] for m in months}
    window = train_months + calibration_months + test_months
    splits = []
    start = 0
    while start + window <= len(months):
        train_m = months[start:start + train_months]
        cal_m = months[start + train_months:start + train_months + calibration_months]
        test_m = months[start + train_months + calibration_months:start + window]
        train = tuple(d for m in train_m for d in by_month[m])
        cal = tuple(d for m in cal_m for d in by_month[m])
        embargo_cut = (date.fromisoformat(max(cal)) +
                       timedelta(days=embargo_days)) if cal else None
        test = tuple(d for m in test_m for d in by_month[m]
                     if embargo_cut is None or date.fromisoformat(d) > embargo_cut)
        if train and cal and test:
            splits.append(WalkForwardSplit(train, cal, test))
        start += test_months
    return splits


class ExperimentRegistry:
    """Registered before execution; failed/stopped runs remain queryable and
    count toward the multiple-testing trial ledger."""

    def __init__(self):
        self._rows = {}

    def register(self, spec):
        eid = spec.experiment_id
        if eid not in self._rows:
            self._rows[eid] = {"experiment_id": eid, "spec": spec,
                               "status": "registered", "detail": None}
        return eid

    def _set(self, eid, status, detail=None):
        if eid not in self._rows:
            raise KeyError(f"experiment {eid} was never registered")
        self._rows[eid]["status"] = status
        self._rows[eid]["detail"] = detail

    def mark_running(self, eid):
        self._set(eid, "running")

    def mark_finished(self, eid, detail=None):
        self._set(eid, "finished", detail)

    def mark_failed(self, eid, detail):
        self._set(eid, "failed", detail)

    def all_experiments(self):
        return [dict(row) for row in self._rows.values()]

    def trial_count(self):
        return len(self._rows)


def run_registered_experiment(
    registry: ImmutableExperimentRegistry,
    spec: ImmutableExperimentSpec,
    run: Callable[[], Mapping],
):
    """Register immutable provenance before invoking the experiment callback.

    A callback exception is recorded as a failed trial and then re-raised; a
    successful callback must return a mapping and is stored as a separate
    immutable terminal outcome.
    """
    if not isinstance(registry, ImmutableExperimentRegistry):
        raise TypeError("registry must be an immutable ExperimentRegistry")
    if not isinstance(spec, ImmutableExperimentSpec):
        raise TypeError("spec must be an immutable ExperimentSpec")
    registry.register_before_run(spec)
    try:
        result = run()
    except Exception as exc:
        # The exception payload may contain provider responses, credentials,
        # prompts, or account identifiers. Persist only the error class as
        # immutable failure provenance; callers still receive the original
        # exception for their own redacted observability boundary.
        registry.fail(spec.experiment_id, type(exc).__name__)
        raise
    if not isinstance(result, Mapping):
        error = TypeError("registered experiment result must be a mapping")
        registry.fail(spec.experiment_id, str(error))
        raise error
    registry.complete_experiment(spec.experiment_id, result)
    return result


class DataManifest:
    """Content-hashed point-in-time inputs. A feature whose historical
    availability (``known_at``) cannot be proven is excluded (B02)."""

    def __init__(self):
        self._entries = {}

    def register(self, name, *, content_hash, known_at):
        if not content_hash:
            raise ValueError(f"{name}: content hash is required")
        if not known_at:
            raise ValueError(
                f"{name}: known_at provenance is required — an unprovable "
                "historical feature is excluded")
        self._entries[str(name)] = {"content_hash": str(content_hash),
                                    "known_at": str(known_at)}

    def freeze(self):
        canonical = json.dumps(self._entries, sort_keys=True,
                               separators=(",", ":"))
        return FrozenManifest(
            entries=tuple(sorted(self._entries.items())),
            manifest_hash="dm-" + hashlib.sha256(canonical.encode()).hexdigest()[:24])


@dataclass(frozen=True)
class FrozenManifest:
    entries: tuple
    manifest_hash: str


class HoldoutAlreadyEvaluatedError(Exception):
    pass


class SealedHoldout:
    def __init__(self, holdout_id):
        self.holdout_id = str(holdout_id)
        self._evaluated = set()

    def evaluate(self, model_family):
        family = str(model_family)
        if family in self._evaluated:
            raise HoldoutAlreadyEvaluatedError(
                f"holdout {self.holdout_id} already consumed by {family} — "
                "one sealed evaluation per model family")
        self._evaluated.add(family)
        return {"holdout_id": self.holdout_id, "model_family": family}


MIN_REGIMES = 3
MIN_UNSEEN_MONTHS = 12


@dataclass(frozen=True)
class StageBGoReport:
    """Fresh-direct LIVE_40 research-policy results ONLY."""
    model_family: str
    active_ceiling: float
    median_annual_active_pp: float
    target_annual_active_pp: float
    bootstrap_lower_bound: float
    information_ratio: float
    deflated_sharpe_probability: float
    max_drawdown_magnitude: float
    profit_factor_after_costs: float
    positive_quarter_fraction: float
    regimes_covered: int
    unseen_sample_months: int

    def __post_init__(self):
        if self.max_drawdown_magnitude < 0:
            raise ValueError(
                "drawdown is a positive magnitude; a negative value is a "
                "schema error and can never pass a <=0.15 comparison")


@dataclass(frozen=True)
class StageBGoDecision:
    go: bool
    reasons: tuple
    grants: str = "build_tasks_12_15_only"


def evaluate_stage_b_go(report):
    reasons = []
    if report.model_family != "graph_fresh_direct":
        reasons.append(
            f"model_family {report.model_family!r} is not fresh_direct — "
            "deterministic-only success requires a new reviewed decision")
    if abs(report.active_ceiling - 0.40) > 1e-9:
        reasons.append("active_ceiling must be the LIVE_40 tier (0.40)")
    checks = [
        ("median_annual_active_pp", report.median_annual_active_pp >= 8.0, ">=8pp"),
        ("target_annual_active_pp", report.target_annual_active_pp >= 10.0, ">=10pp"),
        ("bootstrap_lower_bound", report.bootstrap_lower_bound > 0.0, ">0"),
        ("information_ratio", report.information_ratio >= 0.75, ">=0.75"),
        ("deflated_sharpe_probability",
         report.deflated_sharpe_probability >= 0.95, ">=0.95"),
        ("max_drawdown_magnitude",
         0.0 <= report.max_drawdown_magnitude <= 0.15, "<=0.15"),
        ("profit_factor_after_costs",
         report.profit_factor_after_costs > 1.0, ">1 after all costs"),
        ("positive_quarter_fraction",
         report.positive_quarter_fraction >= 0.60, ">=60% unseen quarters"),
        ("regimes_covered", report.regimes_covered >= MIN_REGIMES,
         f">={MIN_REGIMES} regimes"),
        ("unseen_sample_months", report.unseen_sample_months >= MIN_UNSEEN_MONTHS,
         f">={MIN_UNSEEN_MONTHS} unseen months (power)"),
    ]
    for name, ok, requirement in checks:
        if not ok:
            reasons.append(f"{name} failed ({requirement})")
    return StageBGoDecision(go=not reasons, reasons=tuple(reasons))
