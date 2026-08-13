"""Durable, immutable evidence contracts for deterministic backtest replay.

This module owns only the replay-specific joins between preregistered
experiments, PIT observations, model-evidence rows, and execution receipts.
It intentionally delegates candidate configuration and model/cost provenance
to :mod:`experiment_registry` and immutable request rows to
:mod:`model_evidence`.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
import re
from typing import Any

from experiment_registry import ExperimentSpec, validate_benchmark_manifest
from model_evidence import (
    ModelEvidenceError,
    ModelEvidenceLedger,
    ModelEvidenceRecord,
    _canonical_bytes,
    _digest,
    _freeze,
    _thaw,
)


MATRIX_TABLE = "backtest_replay_matrices"
CALL_TABLE = "backtest_replay_calls"
FIXTURE_TABLE = "backtest_replay_fixtures"
RECEIPT_TABLE = "backtest_replay_receipts"
BUILD_TABLE = "backtest_replay_fixture_builds"


class ReplayError(ValueError):
    """Raised when deterministic replay provenance is incomplete or divergent."""


def _text(name: str, value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        raise ReplayError(f"{name} is required")
    return text


def _named_hash(prefix: str, value: Any) -> str:
    return f"{prefix}{_digest(value)}"


def _utc_timestamp(name: str, value: Any) -> str:
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(_text(name, value).replace("Z", "+00:00"))
        except ValueError as exc:
            raise ReplayError(f"{name} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ReplayError(f"{name} must be timezone-aware")
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _plain(value: Any) -> Any:
    return _thaw(value)


def _same(left: Any, right: Any) -> bool:
    return _canonical_bytes(left) == _canonical_bytes(right)


def _immutable_mapping(name: str, value: Mapping[str, Any]) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or not value:
        raise ReplayError(f"{name} must be a non-empty mapping")
    try:
        return _freeze(_plain(value))
    except ModelEvidenceError as exc:
        raise ReplayError(str(exc)) from exc


def _request_set_hash(semantic_ids: Iterable[str]) -> str:
    values = sorted({_text("semantic ID", item) for item in semantic_ids})
    return _named_hash("request-set-sha256-", {"semantic_ids": values})


def trade_ledger_hash(decisions: Iterable[Any], fills: Iterable[Any]) -> str:
    """Hash ordered decision and fill records without discarding their order."""
    return _named_hash(
        "trade-ledger-sha256-",
        {"version": "trade-ledger-v1", "decisions": list(decisions), "fills": list(fills)},
    )


def validate_replay_source(
    *,
    source_tree_hash: str,
    executed_source_tree_hash: str | None = None,
    dirty: bool = False,
    untracked: bool = False,
    content_manifest: Mapping[str, Any] | None = None,
    content_manifest_hash: str | None = None,
) -> str:
    """Return the executed source identity, failing closed for mutable trees.

    A clean checkout is identified by its preregistered tree hash.  Any dirty
    or untracked input needs a complete file-content manifest, whose canonical
    digest becomes the executable source identity recorded on the receipt.
    """
    expected = _text("source_tree_hash", source_tree_hash)
    executed = _text("executed_source_tree_hash", executed_source_tree_hash or expected)
    if not dirty and not untracked:
        if executed != expected:
            raise ReplayError("executed source tree hash differs from preregistration")
        if content_manifest is not None:
            raise ReplayError("clean executed trees must not replace their tree hash")
        return expected
    if not isinstance(content_manifest, Mapping) or not content_manifest:
        raise ReplayError("dirty or untracked executed trees require a full content manifest")
    files = content_manifest.get("files")
    if not isinstance(files, Mapping) or not files:
        raise ReplayError("full content manifest requires a non-empty files mapping")
    for path, content_hash in files.items():
        _text("content manifest path", path)
        _text("content manifest file hash", content_hash)
    actual = _named_hash("source-content-sha256-", _plain(content_manifest))
    if content_manifest_hash is not None and _text(
        "content_manifest_hash", content_manifest_hash
    ) != actual:
        raise ReplayError("content manifest hash does not match canonical contents")
    return actual


@dataclass(frozen=True)
class ExperimentMatrixManifest:
    """The immutable preregistration binding every arm before any replay work."""

    arms: Mapping[str, ExperimentSpec]
    combinations: Sequence[Any]
    windows: Sequence[Any]
    cost_scenarios: Mapping[str, Mapping[str, ExperimentSpec]]
    fixture_count: int
    arm_recording_order: Sequence[str]
    trial_count: int
    bootstrap_seed: int
    failure_rules: Mapping[str, Any]
    selection_rule: Mapping[str, Any]
    implementation_hashes: Mapping[str, Any]
    matrix_id: str = field(init=False)
    arm_ids: Mapping[str, str] = field(init=False)
    cost_scenario_hashes: Mapping[str, str] = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.arms, Mapping) or not self.arms:
            raise ReplayError("arms must be a non-empty mapping")
        normalized_arms: dict[str, ExperimentSpec] = {}
        for label, spec in self.arms.items():
            arm = _text("arm name", label)
            if not isinstance(spec, ExperimentSpec):
                raise ReplayError("every arm must be an ExperimentSpec")
            normalized_arms[arm] = spec
        order = tuple(_text("arm recording order", arm) for arm in self.arm_recording_order)
        if len(set(order)) != len(order) or set(order) != set(normalized_arms):
            raise ReplayError("arm_recording_order must contain every arm exactly once")
        if isinstance(self.fixture_count, bool) or not isinstance(self.fixture_count, int) or self.fixture_count < 1:
            raise ReplayError("fixture_count must be a positive integer")
        if isinstance(self.trial_count, bool) or not isinstance(self.trial_count, int) or self.trial_count < 1:
            raise ReplayError("trial_count must be a positive integer")
        if isinstance(self.bootstrap_seed, bool) or not isinstance(self.bootstrap_seed, int):
            raise ReplayError("bootstrap_seed must be an integer")
        windows = tuple(_plain(item) for item in self.windows)
        if not windows:
            raise ReplayError("windows must be non-empty")
        combinations = tuple(_plain(item) for item in self.combinations)
        if not combinations:
            raise ReplayError("combinations must be non-empty")
        if not isinstance(self.cost_scenarios, Mapping) or not self.cost_scenarios:
            raise ReplayError("cost_scenarios must be a non-empty mapping")
        costs: dict[str, Mapping[str, ExperimentSpec]] = {}
        hashes: dict[str, str] = {}
        for name, scenario in self.cost_scenarios.items():
            key = _text("cost scenario", name)
            if not isinstance(scenario, Mapping) or set(scenario) != set(normalized_arms):
                raise ReplayError("each cost scenario must bind every matrix arm")
            bound: dict[str, ExperimentSpec] = {}
            cost_hashes = set()
            for arm, spec in scenario.items():
                if not isinstance(spec, ExperimentSpec):
                    raise ReplayError("cost scenarios must bind ExperimentSpec values")
                bound[arm] = spec
                cost_hashes.add(spec.execution_cost_model_hash)
            if len(cost_hashes) != 1:
                raise ReplayError("each union fixture cost scenario requires one cost model")
            if key == "base" and any(
                bound[arm].fingerprint != normalized_arms[arm].fingerprint
                for arm in normalized_arms
            ):
                raise ReplayError("base cost scenario must bind the declared arm experiments")
            costs[key] = bound
            hashes[key] = cost_hashes.pop()
        object.__setattr__(self, "arms", _freeze(normalized_arms))
        object.__setattr__(self, "combinations", _freeze(combinations))
        object.__setattr__(self, "windows", _freeze(windows))
        object.__setattr__(self, "cost_scenarios", _freeze(costs))
        object.__setattr__(self, "arm_recording_order", order)
        for name in ("failure_rules", "selection_rule", "implementation_hashes"):
            object.__setattr__(self, name, _immutable_mapping(name, getattr(self, name)))
        identity = self._identity_payload()
        matrix_id = _named_hash("matrix-sha256-", identity)
        arm_ids = {
            name: _named_hash(
                "arm-sha256-",
                {"matrix_id": matrix_id, "arm": name, "fingerprint": spec.fingerprint},
            )
            for name, spec in normalized_arms.items()
        }
        object.__setattr__(self, "matrix_id", matrix_id)
        object.__setattr__(self, "arm_ids", _freeze(arm_ids))
        object.__setattr__(self, "cost_scenario_hashes", _freeze(hashes))

    def _identity_payload(self) -> dict[str, Any]:
        return {
            "version": "experiment-matrix-v1",
            "arms": {
                name: {"experiment_id": spec.experiment_id, "fingerprint": spec.fingerprint}
                for name, spec in self.arms.items()
            },
            "combinations": _plain(self.combinations),
            "windows": _plain(self.windows),
            "cost_scenarios": {
                scenario: {
                    arm: {
                        "experiment_id": spec.experiment_id,
                        "fingerprint": spec.fingerprint,
                        "execution_cost_model_hash": spec.execution_cost_model_hash,
                    }
                    for arm, spec in specs.items()
                }
                for scenario, specs in self.cost_scenarios.items()
            },
            "fixture_count": self.fixture_count,
            "arm_recording_order": list(self.arm_recording_order),
            "trial_count": self.trial_count,
            "bootstrap_seed": self.bootstrap_seed,
            "failure_rules": _plain(self.failure_rules),
            "selection_rule": _plain(self.selection_rule),
            "implementation_hashes": _plain(self.implementation_hashes),
        }

    def constructor_args(self) -> dict[str, Any]:
        """Return fresh constructor inputs for deterministic variant construction."""
        return {
            "arms": dict(self.arms),
            "combinations": _plain(self.combinations),
            "windows": _plain(self.windows),
            "cost_scenarios": {
                scenario: dict(specs)
                for scenario, specs in self.cost_scenarios.items()
            },
            "fixture_count": self.fixture_count,
            "arm_recording_order": list(self.arm_recording_order),
            "trial_count": self.trial_count,
            "bootstrap_seed": self.bootstrap_seed,
            "failure_rules": _plain(self.failure_rules),
            "selection_rule": _plain(self.selection_rule),
            "implementation_hashes": _plain(self.implementation_hashes),
        }

    def arm_id(self, arm: str) -> str:
        try:
            return self.arm_ids[_text("arm", arm)]
        except KeyError as exc:
            raise ReplayError(f"unknown matrix arm {arm!r}") from exc

    def experiment_for(self, arm_name: str, cost_scenario_id: str) -> ExperimentSpec:
        """Return the preregistered experiment that owns one arm/cost execution."""
        try:
            return self.cost_scenarios[_text("cost_scenario_id", cost_scenario_id)][
                _text("arm_name", arm_name)
            ]
        except KeyError as exc:
            raise ReplayError("arm/cost scenario is not preregistered") from exc

    def to_doc(self) -> dict[str, Any]:
        return {
            "id": self.matrix_id,
            "record_kind": "experiment_matrix",
            "matrix_id": self.matrix_id,
            "arms": {name: spec.to_doc() for name, spec in self.arms.items()},
            "arm_ids": _plain(self.arm_ids),
            "combinations": _plain(self.combinations),
            "windows": _plain(self.windows),
            "cost_scenarios": {
                scenario: {arm: spec.to_doc() for arm, spec in specs.items()}
                for scenario, specs in self.cost_scenarios.items()
            },
            "cost_scenario_hashes": _plain(self.cost_scenario_hashes),
            "fixture_count": self.fixture_count,
            "arm_recording_order": list(self.arm_recording_order),
            "trial_count": self.trial_count,
            "bootstrap_seed": self.bootstrap_seed,
            "failure_rules": _plain(self.failure_rules),
            "selection_rule": _plain(self.selection_rule),
            "implementation_hashes": _plain(self.implementation_hashes),
        }

    @classmethod
    def from_doc(cls, doc: Mapping[str, Any]) -> "ExperimentMatrixManifest":
        expected_keys = {
            "id", "record_kind", "matrix_id", "arms", "arm_ids", "combinations",
            "windows", "cost_scenarios", "cost_scenario_hashes", "fixture_count",
            "arm_recording_order", "trial_count", "bootstrap_seed",
            "failure_rules", "selection_rule", "implementation_hashes",
        }
        if (not isinstance(doc, Mapping) or set(doc) != expected_keys
                or doc.get("record_kind") != "experiment_matrix"):
            raise ReplayError("matrix document shape is invalid")
        try:
            matrix = cls(
                arms={name: ExperimentSpec.from_doc(spec) for name, spec in doc["arms"].items()},
                combinations=doc["combinations"], windows=doc["windows"],
                cost_scenarios={
                    scenario: {
                        arm: ExperimentSpec.from_doc(spec)
                        for arm, spec in specs.items()
                    }
                    for scenario, specs in doc["cost_scenarios"].items()
                }, fixture_count=doc["fixture_count"],
                arm_recording_order=doc["arm_recording_order"], trial_count=doc["trial_count"],
                bootstrap_seed=doc["bootstrap_seed"], failure_rules=doc["failure_rules"],
                selection_rule=doc["selection_rule"], implementation_hashes=doc["implementation_hashes"],
            )
        except KeyError as exc:
            raise ReplayError(f"matrix document is missing {exc.args[0]}") from exc
        if doc.get("matrix_id", doc.get("id")) != matrix.matrix_id:
            raise ReplayError("matrix document identity does not match canonical contents")
        if not _same(doc["arm_ids"], matrix.arm_ids):
            raise ReplayError("matrix arm IDs do not match canonical contents")
        if not _same(doc["cost_scenario_hashes"], matrix.cost_scenario_hashes):
            raise ReplayError("matrix cost scenario hashes do not match canonical contents")
        return matrix


@dataclass(frozen=True)
class ReplayFixture:
    """Content-addressed union of ordered PIT and immutable model evidence."""

    matrix_id: str
    build_id: str
    window: Any
    fixture_ordinal: int
    cost_scenario_id: str
    cost_scenario_hash: str
    pit_chain: Sequence[Mapping[str, Any]]
    model_ledger_hash: str
    arm_request_sets: Mapping[str, Iterable[str]]
    rng_seed_manifest: Mapping[str, Any]
    benchmark_manifest: Mapping[str, Any]
    records: Sequence[ModelEvidenceRecord] = ()
    fixture_id: str = field(init=False)
    arm_request_set_hashes: Mapping[str, str] = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "matrix_id", _text("matrix_id", self.matrix_id))
        object.__setattr__(self, "build_id", _text("build_id", self.build_id))
        object.__setattr__(self, "cost_scenario_id", _text("cost_scenario_id", self.cost_scenario_id))
        object.__setattr__(self, "cost_scenario_hash", _text("cost_scenario_hash", self.cost_scenario_hash))
        if isinstance(self.fixture_ordinal, bool) or not isinstance(self.fixture_ordinal, int) or self.fixture_ordinal < 0:
            raise ReplayError("fixture_ordinal must be a non-negative integer")
        pit = tuple(_immutable_mapping("PIT entry", entry) for entry in self.pit_chain)
        prior_decision = None
        for entry in pit:
            decision = _utc_timestamp("PIT entry.decision_at", entry.get("decision_at"))
            manifest = entry.get("manifest")
            if not isinstance(manifest, Mapping):
                raise ReplayError("PIT entry requires a manifest")
            as_of = _utc_timestamp("PIT manifest.as_of", manifest.get("as_of"))
            if as_of > decision:
                raise ReplayError("PIT manifest.as_of must be at or before its decision")
            if prior_decision is not None and decision < prior_decision:
                raise ReplayError("PIT entries must preserve decision order")
            prior_decision = decision
        requests: dict[str, tuple[str, ...]] = {}
        hashes: dict[str, str] = {}
        for arm, values in self.arm_request_sets.items():
            name = _text("arm", arm)
            ids = tuple(sorted({_text("semantic ID", item) for item in values}))
            requests[name] = ids
            hashes[name] = _request_set_hash(ids)
        if not requests:
            raise ReplayError("arm_request_sets must be non-empty")
        rows = tuple(self.records)
        if any(not isinstance(row, ModelEvidenceRecord) for row in rows):
            raise ReplayError("fixture records must be ModelEvidenceRecord rows")
        if rows:
            ledger = ModelEvidenceLedger(rows)
            if ledger.content_hash != self.model_ledger_hash:
                raise ReplayError("model ledger hash does not match fixture rows")
        object.__setattr__(self, "pit_chain", pit)
        object.__setattr__(self, "arm_request_sets", _freeze(requests))
        object.__setattr__(self, "arm_request_set_hashes", _freeze(hashes))
        object.__setattr__(self, "rng_seed_manifest", _immutable_mapping("rng_seed_manifest", self.rng_seed_manifest))
        validate_benchmark_manifest(self.benchmark_manifest)
        object.__setattr__(self, "benchmark_manifest", _immutable_mapping("benchmark_manifest", self.benchmark_manifest))
        object.__setattr__(self, "records", rows)
        identity = {
            "version": "replay-fixture-v1", "matrix_id": self.matrix_id,
            "window": _plain(self.window), "fixture_ordinal": self.fixture_ordinal,
            "cost_scenario_id": self.cost_scenario_id, "cost_scenario_hash": self.cost_scenario_hash,
            "pit_chain": _plain(pit), "model_ledger_hash": self.model_ledger_hash,
            "arm_request_set_hashes": hashes, "rng_seed_manifest": _plain(self.rng_seed_manifest),
            "benchmark_manifest": _plain(self.benchmark_manifest),
        }
        object.__setattr__(self, "fixture_id", _named_hash("fixture-sha256-", identity))

    def request_set(self, arm: str) -> frozenset[str]:
        try:
            return frozenset(self.arm_request_sets[_text("arm", arm)])
        except KeyError as exc:
            raise ReplayError(f"unknown fixture arm {arm!r}") from exc

    def to_doc(self) -> dict[str, Any]:
        return {
            "id": self.fixture_id, "record_kind": "replay_fixture", "fixture_id": self.fixture_id,
            "matrix_id": self.matrix_id, "build_id": self.build_id, "window": _plain(self.window),
            "fixture_ordinal": self.fixture_ordinal, "cost_scenario_id": self.cost_scenario_id,
            "cost_scenario_hash": self.cost_scenario_hash, "pit_chain": _plain(self.pit_chain),
            "model_ledger_hash": self.model_ledger_hash, "arm_request_sets": _plain(self.arm_request_sets),
            "arm_request_set_hashes": _plain(self.arm_request_set_hashes),
            "rng_seed_manifest": _plain(self.rng_seed_manifest),
            "benchmark_manifest": _plain(self.benchmark_manifest),
        }

    @classmethod
    def from_doc(cls, doc: Mapping[str, Any]) -> "ReplayFixture":
        """Reconstruct and re-address an immutable persisted fixture.

        Call rows are deliberately loaded separately: the fixture manifest owns
        only their declared request sets and aggregate ledger hash.  Derived
        IDs/hashes in the stored document are treated as assertions, never as
        constructor authority.
        """
        if not isinstance(doc, Mapping):
            raise ReplayError("fixture document is missing")
        required = {
            "id", "record_kind", "fixture_id", "matrix_id", "build_id", "window",
            "fixture_ordinal", "cost_scenario_id", "cost_scenario_hash", "pit_chain",
            "model_ledger_hash", "arm_request_sets", "arm_request_set_hashes",
            "rng_seed_manifest", "benchmark_manifest",
        }
        if set(doc) != required or doc.get("record_kind") != "replay_fixture":
            raise ReplayError("fixture document shape is invalid")
        try:
            fixture = cls(
                matrix_id=doc["matrix_id"], build_id=doc["build_id"],
                window=doc["window"], fixture_ordinal=doc["fixture_ordinal"],
                cost_scenario_id=doc["cost_scenario_id"],
                cost_scenario_hash=doc["cost_scenario_hash"],
                pit_chain=doc["pit_chain"], model_ledger_hash=doc["model_ledger_hash"],
                arm_request_sets=doc["arm_request_sets"],
                rng_seed_manifest=doc["rng_seed_manifest"],
                benchmark_manifest=doc["benchmark_manifest"], records=(),
            )
        except (KeyError, TypeError, ModelEvidenceError) as exc:
            raise ReplayError("fixture document contents are invalid") from exc
        if doc.get("id") != fixture.fixture_id or doc.get("fixture_id") != fixture.fixture_id:
            raise ReplayError("fixture document identity does not match canonical contents")
        # build_id is not part of fixture_id, so it must be re-derived: otherwise
        # a writer can repoint a sealed fixture at another build address.
        expected_build = _named_hash("fixture-build-sha256-", {
            "matrix_id": fixture.matrix_id, "window": _plain(fixture.window),
            "fixture_ordinal": fixture.fixture_ordinal,
            "cost_scenario_id": fixture.cost_scenario_id,
        })
        if fixture.build_id != expected_build:
            raise ReplayError("fixture build address does not match canonical contents")
        if not _same(doc.get("arm_request_set_hashes"), fixture.arm_request_set_hashes):
            raise ReplayError("fixture request-set hashes do not match canonical contents")
        return fixture


class FixtureBuild:
    """Mutable construction state addressed by a preregistered build identity."""

    def __init__(self, *, matrix: ExperimentMatrixManifest, window: Any, fixture_ordinal: int,
                 cost_scenario_id: str, rng_seed_manifest: Mapping[str, Any],
                 benchmark_manifest: Mapping[str, Any], store: Any | None = None) -> None:
        if not isinstance(matrix, ExperimentMatrixManifest):
            raise ReplayError("matrix must be an ExperimentMatrixManifest")
        if store is None:
            raise ReplayError("a durable store is required before fixture building")
        store.require_matrix(matrix.matrix_id)
        canonical_window = _plain(window)
        if not any(_same(canonical_window, declared) for declared in matrix.windows):
            raise ReplayError("fixture window is not declared by the matrix")
        if isinstance(fixture_ordinal, bool) or not isinstance(fixture_ordinal, int) or not 0 <= fixture_ordinal < matrix.fixture_count:
            raise ReplayError("fixture_ordinal must be within preregistered fixture_count")
        scenario = _text("cost_scenario_id", cost_scenario_id)
        if scenario not in matrix.cost_scenario_hashes:
            raise ReplayError("cost scenario is not preregistered in the matrix")
        self.matrix = matrix
        self.matrix_id = matrix.matrix_id
        self.window = canonical_window
        self.fixture_ordinal = fixture_ordinal
        self.cost_scenario_id = scenario
        self.rng_seed_manifest = _immutable_mapping("rng_seed_manifest", rng_seed_manifest)
        validate_benchmark_manifest(benchmark_manifest)
        self.benchmark_manifest = _immutable_mapping("benchmark_manifest", benchmark_manifest)
        self.build_id = _named_hash("fixture-build-sha256-", {
            "matrix_id": matrix.matrix_id, "window": self.window,
            "fixture_ordinal": fixture_ordinal, "cost_scenario_id": scenario,
        })
        store.reserve_build(
            self.build_id,
            {
                "matrix_id": matrix.matrix_id,
                "window": self.window,
                "fixture_ordinal": fixture_ordinal,
                "cost_scenario_id": scenario,
                "cost_scenario_hash": matrix.cost_scenario_hashes[scenario],
            },
        )
        self._pit_chain: list[Mapping[str, Any]] = []
        self._ledger = ModelEvidenceLedger()
        self._arm_requests: dict[str, set[str]] = {arm: set() for arm in matrix.arms}
        self._sealed: ReplayFixture | None = None

    @property
    def ledger(self) -> ModelEvidenceLedger:
        return self._ledger

    def _assert_open(self) -> None:
        if self._sealed is not None:
            raise ReplayError("sealed fixture builds cannot be modified")

    def record_pit(self, decision_at: Any, manifest: Mapping[str, Any]) -> None:
        self._assert_open()
        if not isinstance(manifest, Mapping):
            raise ReplayError("PIT manifest must be a mapping")
        decision = _utc_timestamp("decision_at", decision_at)
        as_of = _utc_timestamp("PIT manifest.as_of", manifest.get("as_of"))
        if as_of > decision:
            raise ReplayError("PIT manifest.as_of must be at or before its decision")
        if self._pit_chain and decision < self._pit_chain[-1]["decision_at"]:
            raise ReplayError("PIT entries must preserve decision order")
        entry = {"decision_at": decision, "manifest": _plain(manifest)}
        self._pit_chain.append(_immutable_mapping("PIT entry", entry))

    def record_model_row(self, arm: str, record: ModelEvidenceRecord) -> None:
        self._assert_open()
        name = _text("arm", arm)
        if name not in self._arm_requests:
            raise ReplayError(f"unknown matrix arm {arm!r}")
        try:
            self._ledger.publish(record)
        except ModelEvidenceError as exc:
            raise ReplayError(str(exc)) from exc
        self._arm_requests[name].add(record.semantic_id)

    def adopt_session_occurrences(self, arm: str, semantic_ids) -> None:
        """Bind occurrences the evidence session published for this arm.

        Production records rows through the process-global session rather than
        through `record_model_row`, so without this the sealed fixture would
        declare an empty request set for a run that really made calls.
        """
        self._assert_open()
        name = _text("arm", arm)
        if name not in self._arm_requests:
            raise ReplayError(f"unknown matrix arm {arm!r}")
        for semantic_id in semantic_ids:
            identifier = _text("semantic ID", semantic_id)
            if self._ledger.get(identifier) is None:
                raise ReplayError("session occurrence is absent from the fixture ledger")
            self._arm_requests[name].add(identifier)

    def seal(self) -> ReplayFixture:
        if self._sealed is not None:
            return self._sealed
        self._sealed = ReplayFixture(
            matrix_id=self.matrix.matrix_id, build_id=self.build_id, window=self.window,
            fixture_ordinal=self.fixture_ordinal, cost_scenario_id=self.cost_scenario_id,
            cost_scenario_hash=self.matrix.cost_scenario_hashes[self.cost_scenario_id],
            pit_chain=tuple(self._pit_chain), model_ledger_hash=self._ledger.content_hash,
            arm_request_sets=self._arm_requests, rng_seed_manifest=self.rng_seed_manifest,
            benchmark_manifest=self.benchmark_manifest, records=self._ledger.records,
        )
        return self._sealed


@dataclass(frozen=True)
class ReplayReceipt:
    """Arm-local execution receipt; promotion is permitted only with all audits."""

    matrix: ExperimentMatrixManifest
    arm_name: str
    arm_id: str
    fixture: ReplayFixture
    experiment: ExperimentSpec
    executed_source_tree_hash: str
    dependency_runtime_digest: str
    executed_cost_model_hash: str
    trade_ledger_hash: str
    replay_audit: Mapping[str, Any]
    pit_audit: bool
    execution_audit: bool
    benchmark_audit: bool
    accounting_audit: bool
    executed_content_manifest: Mapping[str, Any] | None = None
    receipt_id: str = field(init=False)
    executed_source_identity: str = field(init=False)
    promotion_eligible: bool = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.matrix, ExperimentMatrixManifest) or not isinstance(self.fixture, ReplayFixture):
            raise ReplayError("receipt requires an immutable matrix and fixture")
        arm_name = _text("arm_name", self.arm_name)
        arm_id = _text("arm_id", self.arm_id)
        if arm_name not in self.matrix.arms:
            raise ReplayError("receipt arm is not declared by its matrix")
        if arm_id != self.matrix.arm_id(arm_name):
            raise ReplayError("receipt arm ID differs from its preregistered matrix arm ID")
        selected = self.matrix.experiment_for(arm_name, self.fixture.cost_scenario_id)
        if selected.fingerprint != self.experiment.fingerprint:
            raise ReplayError("receipt experiment fingerprint differs from matrix arm/cost scenario")
        if self.fixture.matrix_id != self.matrix.matrix_id or arm_name not in self.fixture.arm_request_sets:
            raise ReplayError("receipt fixture is not a union fixture for this matrix arm")
        source_identity = validate_replay_source(
            source_tree_hash=self.experiment.source_tree_hash,
            executed_source_tree_hash=self.executed_source_tree_hash,
            dirty=self.executed_content_manifest is not None,
            content_manifest=self.executed_content_manifest,
        )
        if self.executed_cost_model_hash != self.experiment.execution_cost_model_hash:
            raise ReplayError("receipt execution cost model differs from preregistered experiment")
        if self.fixture.cost_scenario_hash != self.experiment.execution_cost_model_hash:
            raise ReplayError("receipt fixture cost scenario differs from preregistered experiment")
        runtime_digest = _text("dependency_runtime_digest", self.dependency_runtime_digest)
        if re.fullmatch(r"sha256:[0-9a-f]{64}", runtime_digest) is None:
            raise ReplayError("dependency_runtime_digest must be a sha256 digest")
        ledger_hash = _text("trade_ledger_hash", self.trade_ledger_hash)
        if re.fullmatch(r"trade-ledger-sha256-[0-9a-f]{64}", ledger_hash) is None:
            raise ReplayError("trade ledger hash must be a canonical trade-ledger-sha256 digest")
        audit = _immutable_mapping("replay_audit", self.replay_audit)
        # A receipt may not assert completeness against a ledger the fixture
        # does not own; otherwise "complete" is an unverifiable self-claim.
        claimed_ledger = audit.get("ledger_content_hash")
        if claimed_ledger is not None and claimed_ledger != self.fixture.model_ledger_hash:
            raise ReplayError("replay audit ledger hash differs from its fixture")
        if audit.get("complete") is True and claimed_ledger is None:
            raise ReplayError("a complete replay audit must bind its ledger hash")
        all_audits = (
            audit.get("complete") is True and self.pit_audit is True and self.execution_audit is True
            and self.benchmark_audit is True and self.accounting_audit is True
        )
        identity = {
            "version": "replay-receipt-v1", "matrix_id": self.matrix.matrix_id,
            "arm_id": arm_id, "fixture_id": self.fixture.fixture_id,
            "experiment_fingerprint": self.experiment.fingerprint,
            "executed_source_identity": source_identity,
            "dependency_runtime_digest": runtime_digest,
            "execution_cost_model_hash": self.executed_cost_model_hash,
            "trade_ledger_hash": ledger_hash,
            "replay_audit": _plain(audit), "pit_audit": self.pit_audit,
            "execution_audit": self.execution_audit, "benchmark_audit": self.benchmark_audit,
            "accounting_audit": self.accounting_audit,
        }
        object.__setattr__(self, "arm_name", arm_name)
        object.__setattr__(self, "arm_id", arm_id)
        object.__setattr__(self, "replay_audit", audit)
        object.__setattr__(self, "executed_source_identity", source_identity)
        object.__setattr__(self, "promotion_eligible", all_audits)
        object.__setattr__(self, "receipt_id", _named_hash("receipt-sha256-", identity))

    def to_doc(self) -> dict[str, Any]:
        return {
            "id": self.receipt_id, "record_kind": "replay_receipt", "receipt_id": self.receipt_id,
            "matrix_id": self.matrix.matrix_id, "arm_name": self.arm_name,
            "arm_id": self.arm_id,
            "fixture_id": self.fixture.fixture_id, "experiment_id": self.experiment.experiment_id,
            "experiment_fingerprint": self.experiment.fingerprint,
            "executed_source_tree_hash": self.executed_source_tree_hash,
            "executed_source_identity": self.executed_source_identity,
            "dependency_runtime_digest": self.dependency_runtime_digest,
            "execution_cost_model_hash": self.executed_cost_model_hash,
            "trade_ledger_hash": self.trade_ledger_hash, "replay_audit": _plain(self.replay_audit),
            "pit_audit": self.pit_audit, "execution_audit": self.execution_audit,
            "benchmark_audit": self.benchmark_audit, "accounting_audit": self.accounting_audit,
            "promotion_eligible": self.promotion_eligible,
        }


class InMemoryReplayStore:
    """Test-friendly immutable store with the production publication ordering."""

    def __init__(self) -> None:
        self._matrices: dict[str, ExperimentMatrixManifest] = {}
        self._calls: dict[str, ModelEvidenceRecord] = {}
        self._builds: dict[str, Mapping[str, Any]] = {}
        self._fixtures: dict[str, ReplayFixture] = {}
        self._receipts: dict[str, ReplayReceipt] = {}
        self.write_order: tuple[str, ...] = ()

    def _wrote(self, kind: str) -> None:
        self.write_order += (kind,)

    def publish_matrix(self, matrix: ExperimentMatrixManifest) -> ExperimentMatrixManifest:
        prior = self._matrices.get(matrix.matrix_id)
        if prior is not None:
            if not _same(prior.to_doc(), matrix.to_doc()):
                raise ReplayError("divergent immutable matrix row")
            return prior
        self._matrices[matrix.matrix_id] = matrix
        self._wrote("matrix")
        return matrix

    def require_matrix(self, matrix_id: str) -> ExperimentMatrixManifest:
        try:
            return self._matrices[matrix_id]
        except KeyError as exc:
            raise ReplayError("matrix must be preregistered before fixture building") from exc

    def get_matrix(self, matrix_id: str) -> ExperimentMatrixManifest | None:
        return self._matrices.get(matrix_id)

    def get_call(self, semantic_id: str) -> ModelEvidenceRecord | None:
        return self._calls.get(semantic_id)

    def get_fixture(self, fixture_id: str) -> ReplayFixture | None:
        return self._fixtures.get(fixture_id)

    def load_replay_fixture(
        self, fixture_id: str, arm_name: str
    ) -> tuple[ReplayFixture, ModelEvidenceLedger]:
        """Load exactly one arm's declared, validated replay ledger."""
        fixture = self.get_fixture(_text("fixture_id", fixture_id))
        if fixture is None:
            raise ReplayError("replay fixture is not published")
        request_ids = fixture.request_set(arm_name)
        records = []
        for semantic_id in sorted(request_ids):
            row = self.get_call(semantic_id)
            if row is None:
                raise ReplayError("replay fixture call row is missing")
            records.append(row)
        ledger = ModelEvidenceLedger(records)
        # The fixture ledger is the union across arms.  Recompute that union
        # from storage, not only this arm's subset, before trusting the fixture.
        union_ids = set().union(*(
            fixture.request_set(name) for name in fixture.arm_request_sets
        ))
        union_rows = []
        for semantic_id in sorted(union_ids):
            row = self.get_call(semantic_id)
            if row is None:
                raise ReplayError("replay fixture call row is missing")
            union_rows.append(row)
        if ModelEvidenceLedger(union_rows).content_hash != fixture.model_ledger_hash:
            raise ReplayError("replay fixture model ledger hash mismatch")
        return fixture, ledger

    def reserve_build(self, build_id: str, declaration: Mapping[str, Any]) -> Mapping[str, Any]:
        """Durably reserve one matrix/window/ordinal/cost construction address."""
        identifier = _text("build_id", build_id)
        frozen = _immutable_mapping("build declaration", declaration)
        prior = self._builds.get(identifier)
        if prior is not None:
            if not _same(prior, frozen):
                raise ReplayError("divergent immutable fixture build row")
            return prior
        self._builds[identifier] = frozen
        self._wrote("build")
        return frozen

    def reserved_build(self, build_id: str) -> Mapping[str, Any] | None:
        return self._builds.get(build_id)

    def publish_call(self, row: ModelEvidenceRecord) -> ModelEvidenceRecord:
        prior = self._calls.get(row.semantic_id)
        if prior is not None:
            if prior.content_hash != row.content_hash:
                raise ReplayError("divergent immutable row for semantic ID " + row.semantic_id)
            return prior
        self._calls[row.semantic_id] = row
        self._wrote("call")
        return row

    def _preflight_fixture(self, fixture: ReplayFixture) -> tuple[ModelEvidenceRecord, ...]:
        matrix = self.require_matrix(fixture.matrix_id)
        declaration = self._builds.get(fixture.build_id)
        if declaration is None:
            raise ReplayError("fixture build must be durably reserved before sealing")
        expected_build = {
            "matrix_id": fixture.matrix_id,
            "window": fixture.window,
            "fixture_ordinal": fixture.fixture_ordinal,
            "cost_scenario_id": fixture.cost_scenario_id,
            "cost_scenario_hash": fixture.cost_scenario_hash,
        }
        if not _same(declaration, expected_build):
            raise ReplayError("fixture does not match its preregistered build address")
        if fixture.cost_scenario_id not in matrix.cost_scenario_hashes:
            raise ReplayError("fixture cost scenario is not preregistered")
        if fixture.cost_scenario_hash != matrix.cost_scenario_hashes[fixture.cost_scenario_id]:
            raise ReplayError("fixture cost scenario hash differs from preregistration")
        if set(fixture.arm_request_sets) != set(matrix.arms):
            raise ReplayError("fixture request declarations must cover every matrix arm")
        candidates = dict(self._calls)
        unpublished: list[ModelEvidenceRecord] = []
        for row in fixture.records:
            prior = candidates.get(row.semantic_id)
            if prior is not None and prior.content_hash != row.content_hash:
                raise ReplayError("divergent immutable row for semantic ID " + row.semantic_id)
            if prior is None:
                candidates[row.semantic_id] = row
                unpublished.append(row)
        ids = set().union(*(fixture.request_set(arm) for arm in fixture.arm_request_sets))
        missing = sorted(semantic_id for semantic_id in ids if semantic_id not in candidates)
        if missing:
            raise ReplayError("fixture manifest cannot precede immutable call rows")
        ledger = ModelEvidenceLedger(candidates[semantic_id] for semantic_id in sorted(ids))
        if ledger.content_hash != fixture.model_ledger_hash:
            raise ReplayError("fixture model ledger hash differs from published call rows")
        return tuple(unpublished)

    def publish_fixture(self, fixture: ReplayFixture) -> ReplayFixture:
        unpublished = self._preflight_fixture(fixture)
        for row in unpublished:
            self.publish_call(row)
        prior = self._fixtures.get(fixture.fixture_id)
        if prior is not None:
            if not _same(prior.to_doc(), fixture.to_doc()):
                raise ReplayError("divergent immutable fixture row")
            return prior
        self._fixtures[fixture.fixture_id] = fixture
        self._wrote("fixture")
        return fixture

    def publish_receipt(self, receipt: ReplayReceipt) -> ReplayReceipt:
        self.require_matrix(receipt.matrix.matrix_id)
        fixture = self._fixtures.get(receipt.fixture.fixture_id)
        if fixture is None:
            raise ReplayError("receipt manifest cannot precede its fixture manifest")
        prior = self._receipts.get(receipt.receipt_id)
        if prior is not None:
            if not _same(prior.to_doc(), receipt.to_doc()):
                raise ReplayError("divergent immutable receipt row")
            return prior
        self._receipts[receipt.receipt_id] = receipt
        self._wrote("receipt")
        return receipt


class RethinkReplayStore(InMemoryReplayStore):
    """Immutable replay persistence adapter over the existing Rethink backend seam."""

    def __init__(self, backend: Any) -> None:
        super().__init__()
        self.backend = backend

    def _persist(self, table: str, doc: Mapping[str, Any]) -> None:
        prior = self.backend.insert_record(table, dict(doc), durability="hard")
        if prior is not None and not _same(prior, doc):
            raise ReplayError(f"divergent immutable row {doc['id']!r} in {table}")

    def publish_matrix(self, matrix: ExperimentMatrixManifest) -> ExperimentMatrixManifest:
        existing = self.get_matrix(matrix.matrix_id)
        if existing is not None:
            if not _same(existing.to_doc(), matrix.to_doc()):
                raise ReplayError("divergent immutable matrix row")
            return existing
        self._persist(MATRIX_TABLE, matrix.to_doc())
        return super().publish_matrix(matrix)

    def get_matrix(self, matrix_id: str) -> ExperimentMatrixManifest | None:
        cached = super().get_matrix(matrix_id)
        if cached is not None:
            return cached
        doc = self.backend.get_record(MATRIX_TABLE, matrix_id)
        if doc is None:
            return None
        matrix = ExperimentMatrixManifest.from_doc(doc)
        self._matrices[matrix.matrix_id] = matrix
        return matrix

    @staticmethod
    def _call_from_doc(doc: Mapping[str, Any]) -> ModelEvidenceRecord:
        if not isinstance(doc, Mapping):
            raise ReplayError("model evidence call document is missing")
        required = {
            "id", "record_kind", "semantic_id", "envelope", "context",
            "outcome", "response_metadata", "content_hash",
        }
        if set(doc) != required or doc.get("record_kind") != "model_evidence_call":
            raise ReplayError("model evidence call document shape is invalid")
        try:
            from model_evidence import ModelEvidenceContext
            row = ModelEvidenceRecord(
                semantic_id=doc["semantic_id"], envelope=doc["envelope"],
                context=ModelEvidenceContext(**doc["context"]),
                outcome=doc["outcome"], response_metadata=doc["response_metadata"],
            )
        except (KeyError, TypeError, ModelEvidenceError) as exc:
            raise ReplayError("model evidence call document contents are invalid") from exc
        if doc.get("id") != row.semantic_id or doc.get("content_hash") != row.content_hash:
            raise ReplayError("model evidence call identity does not match canonical contents")
        return row

    def get_call(self, semantic_id: str) -> ModelEvidenceRecord | None:
        cached = super().get_call(semantic_id)
        if cached is not None:
            return cached
        doc = self.backend.get_record(CALL_TABLE, semantic_id)
        if doc is None:
            return None
        row = self._call_from_doc(doc)
        self._calls[row.semantic_id] = row
        return row

    def get_fixture(self, fixture_id: str) -> ReplayFixture | None:
        cached = super().get_fixture(fixture_id)
        if cached is not None:
            return cached
        doc = self.backend.get_record(FIXTURE_TABLE, fixture_id)
        if doc is None:
            return None
        fixture = ReplayFixture.from_doc(doc)
        self._fixtures[fixture.fixture_id] = fixture
        return fixture

    def reserve_build(self, build_id: str, declaration: Mapping[str, Any]) -> Mapping[str, Any]:
        doc = {
            "id": _text("build_id", build_id),
            "record_kind": "replay_fixture_build",
            **_plain(_immutable_mapping("build declaration", declaration)),
        }
        self._persist(BUILD_TABLE, doc)
        return super().reserve_build(build_id, declaration)

    def require_matrix(self, matrix_id: str) -> ExperimentMatrixManifest:
        matrix = self.get_matrix(matrix_id)
        if matrix is None:
            raise ReplayError("matrix must be preregistered before fixture building")
        return matrix

    def publish_call(self, row: ModelEvidenceRecord) -> ModelEvidenceRecord:
        prior = self._calls.get(row.semantic_id)
        if prior is not None:
            if prior.content_hash != row.content_hash:
                raise ReplayError("divergent immutable row for semantic ID " + row.semantic_id)
            return prior
        doc = {"id": row.semantic_id, "record_kind": "model_evidence_call", **row.canonical_value(), "content_hash": row.content_hash}
        self._persist(CALL_TABLE, doc)
        return super().publish_call(row)

    def publish_fixture(self, fixture: ReplayFixture) -> ReplayFixture:
        # Validate without writes, then publish calls before the final manifest.
        unpublished = self._preflight_fixture(fixture)
        for row in unpublished:
            self.publish_call(row)
        self._persist(FIXTURE_TABLE, fixture.to_doc())
        return super().publish_fixture(fixture)

    def publish_receipt(self, receipt: ReplayReceipt) -> ReplayReceipt:
        self.require_matrix(receipt.matrix.matrix_id)
        if receipt.fixture.fixture_id not in self._fixtures:
            persisted = self.backend.get_record(FIXTURE_TABLE, receipt.fixture.fixture_id)
            if persisted is None or not _same(persisted, receipt.fixture.to_doc()):
                raise ReplayError("receipt manifest cannot precede its fixture manifest")
            self._fixtures[receipt.fixture.fixture_id] = receipt.fixture
        self._persist(RECEIPT_TABLE, receipt.to_doc())
        return super().publish_receipt(receipt)
