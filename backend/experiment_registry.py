"""Immutable experiment-attempt registration and provenance.

An experiment ID identifies one attempt, not one configuration. Repeating the
same configuration therefore uses a new experiment ID while retaining the same
``fingerprint``. Registrations and terminal outcomes are separate append-only
records so completing or failing a run never rewrites its preregistration.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from types import MappingProxyType
from typing import Any, Callable


class ExperimentRegistryError(RuntimeError):
    """Base class for registry contract violations."""


class DuplicateExperimentError(ExperimentRegistryError):
    """An experiment ID was already registered."""


class ExperimentAlreadyTerminalError(ExperimentRegistryError):
    """A terminal outcome already exists and cannot be overwritten."""


class FrozenDict(Mapping):
    """A recursively immutable mapping with deterministic key iteration."""

    __slots__ = ("_data",)

    def __init__(self, value: Mapping):
        data = {
            str(key): _freeze_json(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
        self._data = MappingProxyType(data)

    def __getitem__(self, key):
        return self._data[key]

    def __iter__(self):
        return iter(self._data)

    def __len__(self):
        return len(self._data)

    def __repr__(self):
        return f"FrozenDict({dict(self._data)!r})"


def _freeze_json(value):
    if isinstance(value, FrozenDict):
        return value
    if isinstance(value, Mapping):
        return FrozenDict(value)
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json(item) for item in value)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError("provenance datetimes must be timezone-aware")
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("provenance cannot contain NaN or infinity")
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(
        f"provenance values must be JSON-compatible, got {type(value).__name__}"
    )


def _thaw_json(value):
    if isinstance(value, Mapping):
        return {str(key): _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return value


def _canonical_json(value) -> str:
    def normalize_numbers(item):
        if isinstance(item, bool):
            return item
        if isinstance(item, int):
            return item
        if isinstance(item, float):
            return float(item)
        if isinstance(item, dict):
            return {
                str(key): normalize_numbers(child)
                for key, child in item.items()
            }
        if isinstance(item, list):
            return [normalize_numbers(child) for child in item]
        return item

    return json.dumps(
        normalize_numbers(_thaw_json(_freeze_json(value))),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _require_text(name: str, value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{name} is required")
    return text


def _require_aware_timestamp(name: str, value: Any) -> None:
    if isinstance(value, datetime):
        parsed = value
    else:
        text = _require_text(name, value)
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"{name} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{name} must be timezone-aware")


def _validate_source_manifest(name: str, value: Mapping[str, Any]) -> None:
    required = ("manifest_id", "source_hashes", "created_at")
    missing = [field for field in required if field not in value]
    if missing:
        raise ValueError(f"{name} missing required field {missing[0]}")
    _require_text(f"{name}.manifest_id", value["manifest_id"])
    source_hashes = value["source_hashes"]
    if not isinstance(source_hashes, Mapping) or not source_hashes:
        raise ValueError(f"{name}.source_hashes must be a non-empty mapping")
    for source, content_hash in source_hashes.items():
        _require_text(f"{name}.source_hashes key", source)
        _require_text(
            f"{name}.source_hashes[{str(source)!r}]",
            content_hash,
        )
    _require_aware_timestamp(f"{name}.created_at", value["created_at"])


_SPY_CONTENT_HASH = re.compile(r"^spy-sha256-[0-9a-f]{64}$")


def validate_benchmark_manifest(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the immutable adjusted-SPY promotion contract."""
    if not isinstance(value, Mapping) or not value:
        raise ValueError("benchmark_manifest must be a non-empty mapping")
    manifest = dict(value)
    required = (
        "manifest_id",
        "symbol",
        "timeframe",
        "adjustment",
        "price_field",
        "total_return",
        "feed",
        "start_date",
        "end_date",
        "valuation_rule",
        "valuation_timestamps",
        "content_hash",
    )
    missing = [field for field in required if field not in manifest]
    if missing:
        raise ValueError(
            f"benchmark_manifest missing required field {missing[0]}"
        )
    _require_text("benchmark_manifest.manifest_id", manifest["manifest_id"])
    expected = {
        "symbol": "SPY",
        "timeframe": "1Day",
        "adjustment": "all",
        "price_field": "c",
        "total_return": True,
        "valuation_rule": "xnys_session_close",
    }
    for field, required_value in expected.items():
        if manifest[field] != required_value:
            raise ValueError(
                f"benchmark_manifest.{field} must be {required_value!r}"
            )
    if str(manifest["feed"]).strip().lower() not in {"iex", "sip"}:
        raise ValueError("benchmark_manifest.feed must be 'iex' or 'sip'")
    try:
        start = date.fromisoformat(str(manifest["start_date"]))
        end = date.fromisoformat(str(manifest["end_date"]))
    except ValueError as exc:
        raise ValueError(
            "benchmark_manifest start_date and end_date must be ISO dates"
        ) from exc
    if end < start:
        raise ValueError(
            "benchmark_manifest.end_date must not precede start_date"
        )
    valuation_timestamps = manifest["valuation_timestamps"]
    if (
        not isinstance(valuation_timestamps, (list, tuple))
        or not valuation_timestamps
    ):
        raise ValueError(
            "benchmark_manifest.valuation_timestamps must be a non-empty list"
        )
    canonical_timestamps = []
    for raw_timestamp in valuation_timestamps:
        text = _require_text(
            "benchmark_manifest.valuation_timestamps item",
            raw_timestamp,
        )
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(
                "benchmark_manifest.valuation_timestamps items must be "
                "ISO-8601 timestamps"
            ) from exc
        if parsed.tzinfo is None:
            raise ValueError(
                "benchmark_manifest.valuation_timestamps items must be "
                "timezone-aware"
            )
        canonical = (
            parsed.astimezone(timezone.utc)
            .isoformat()
            .replace("+00:00", "Z")
        )
        if text != canonical:
            raise ValueError(
                "benchmark_manifest.valuation_timestamps items must use "
                "canonical UTC Z form"
            )
        if not start <= parsed.date() <= end:
            raise ValueError(
                "benchmark_manifest.valuation_timestamps must stay within "
                "the manifest date window"
            )
        canonical_timestamps.append(canonical)
    if canonical_timestamps != sorted(set(canonical_timestamps)):
        raise ValueError(
            "benchmark_manifest.valuation_timestamps must be unique and sorted"
        )
    content_hash = str(manifest["content_hash"] or "").strip()
    if _SPY_CONTENT_HASH.fullmatch(content_hash) is None:
        raise ValueError(
            "benchmark_manifest.content_hash must be a canonical "
            "spy-sha256 digest"
        )
    return manifest


def _validate_execution_cost_model(value: Mapping[str, Any]) -> None:
    required = (
        "version",
        "spread_bps",
        "slippage_bps",
        "fee_bps",
        "latency_seconds",
    )
    missing = [field for field in required if field not in value]
    if missing:
        raise ValueError(
            f"execution_cost_model missing required field {missing[0]}"
        )
    _require_text("execution_cost_model.version", value["version"])
    for field in required[1:]:
        raw = value[field]
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise ValueError(
                f"execution_cost_model.{field} must be a finite number"
            )
        number = float(raw)
        if not math.isfinite(number) or number < 0:
            raise ValueError(
                f"execution_cost_model.{field} must be finite and nonnegative"
            )
        if field.endswith("_bps") and number >= 10_000:
            raise ValueError(
                f"execution_cost_model.{field} must be less than 10000"
            )


@dataclass(frozen=True)
class ExperimentSpec:
    """Complete effective inputs for exactly one experiment attempt."""

    experiment_id: str
    search_scope: str
    commit_sha: str
    source_tree_hash: str
    effective_config: Mapping[str, Any]
    model_provider: str
    model_name: str
    prompt_hashes: Mapping[str, Any]
    model_settings: Mapping[str, Any]
    seed: int
    predeclared_repeats: int
    dataset_manifest: Mapping[str, Any]
    graph_manifest: Mapping[str, Any]
    universe_manifest: Mapping[str, Any]
    benchmark_manifest: Mapping[str, Any]
    execution_cost_model: Mapping[str, Any]
    start_date: str
    end_date: str
    fold: str
    actor: str
    parent_experiment_id: str | None = None
    effective_config_hash: str = field(init=False)
    fingerprint: str = field(init=False)

    def __post_init__(self):
        for name in (
            "experiment_id",
            "search_scope",
            "commit_sha",
            "source_tree_hash",
            "model_provider",
            "model_name",
            "start_date",
            "end_date",
            "fold",
            "actor",
        ):
            object.__setattr__(self, name, _require_text(name, getattr(self, name)))
        if self.parent_experiment_id is not None:
            object.__setattr__(
                self,
                "parent_experiment_id",
                _require_text("parent_experiment_id", self.parent_experiment_id),
            )
        if isinstance(self.seed, bool) or not isinstance(self.seed, int):
            raise ValueError("seed must be an integer")
        if (
            isinstance(self.predeclared_repeats, bool)
            or not isinstance(self.predeclared_repeats, int)
        ):
            raise ValueError("predeclared_repeats must be an integer")
        repeats = self.predeclared_repeats
        if repeats < 1:
            raise ValueError("predeclared_repeats must be positive")
        object.__setattr__(self, "predeclared_repeats", repeats)

        for name in (
            "dataset_manifest",
            "graph_manifest",
            "universe_manifest",
        ):
            value = getattr(self, name)
            if not isinstance(value, Mapping) or not value:
                raise ValueError(f"{name} must be a non-empty mapping")
            _validate_source_manifest(name, value)
        validate_benchmark_manifest(self.benchmark_manifest)
        if (
            not isinstance(self.execution_cost_model, Mapping)
            or not self.execution_cost_model
        ):
            raise ValueError(
                "execution_cost_model must be a non-empty mapping"
            )
        _validate_execution_cost_model(self.execution_cost_model)

        for name in (
            "effective_config",
            "prompt_hashes",
            "model_settings",
            "dataset_manifest",
            "graph_manifest",
            "universe_manifest",
            "benchmark_manifest",
            "execution_cost_model",
        ):
            value = getattr(self, name)
            if not isinstance(value, Mapping) or not value:
                raise ValueError(f"{name} must be a non-empty mapping")
            object.__setattr__(self, name, FrozenDict(value))

        config_digest = hashlib.sha256(
            _canonical_json(self.effective_config).encode("utf-8")
        ).hexdigest()
        object.__setattr__(
            self,
            "effective_config_hash",
            f"config-sha256-{config_digest}",
        )

        start = date.fromisoformat(self.start_date)
        end = date.fromisoformat(self.end_date)
        if end < start:
            raise ValueError("end_date must not precede start_date")
        benchmark = self.benchmark_manifest
        if benchmark["start_date"] != self.start_date:
            raise ValueError(
                "benchmark_manifest.start_date must match experiment start_date"
            )
        if benchmark["end_date"] != self.end_date:
            raise ValueError(
                "benchmark_manifest.end_date must match experiment end_date"
            )

        fingerprint_payload = {
            "parent_experiment_id": self.parent_experiment_id,
            "search_scope": self.search_scope,
            "commit_sha": self.commit_sha,
            "source_tree_hash": self.source_tree_hash,
            "effective_config": self.effective_config,
            "model_provider": self.model_provider,
            "model_name": self.model_name,
            "prompt_hashes": self.prompt_hashes,
            "model_settings": self.model_settings,
            "seed": self.seed,
            "predeclared_repeats": self.predeclared_repeats,
            "dataset_manifest": self.dataset_manifest,
            "graph_manifest": self.graph_manifest,
            "universe_manifest": self.universe_manifest,
            "benchmark_manifest": self.benchmark_manifest,
            "execution_cost_model": self.execution_cost_model,
            "start_date": self.start_date,
            "end_date": self.end_date,
            "fold": self.fold,
        }
        digest = hashlib.sha256(
            _canonical_json(fingerprint_payload).encode("utf-8")
        ).hexdigest()
        object.__setattr__(self, "fingerprint", f"expfp-{digest}")

    def to_doc(self) -> dict:
        return {
            "experiment_id": self.experiment_id,
            "parent_experiment_id": self.parent_experiment_id,
            "search_scope": self.search_scope,
            "commit_sha": self.commit_sha,
            "source_tree_hash": self.source_tree_hash,
            "effective_config": _thaw_json(self.effective_config),
            "effective_config_hash": self.effective_config_hash,
            "model_provider": self.model_provider,
            "model_name": self.model_name,
            "prompt_hashes": _thaw_json(self.prompt_hashes),
            "model_settings": _thaw_json(self.model_settings),
            "seed": self.seed,
            "predeclared_repeats": self.predeclared_repeats,
            "dataset_manifest": _thaw_json(self.dataset_manifest),
            "graph_manifest": _thaw_json(self.graph_manifest),
            "universe_manifest": _thaw_json(self.universe_manifest),
            "benchmark_manifest": _thaw_json(self.benchmark_manifest),
            "execution_cost_model": _thaw_json(self.execution_cost_model),
            "start_date": self.start_date,
            "end_date": self.end_date,
            "fold": self.fold,
            "actor": self.actor,
            "fingerprint": self.fingerprint,
        }

    @property
    def execution_cost_model_hash(self) -> str:
        """Canonical cost identity for replay receipts without a duplicate field."""
        digest = hashlib.sha256(
            _canonical_json(self.execution_cost_model).encode("utf-8")
        ).hexdigest()
        return f"cost-sha256-{digest}"

    @property
    def source_manifest_chain(self) -> tuple[FrozenDict, FrozenDict, FrozenDict]:
        """Ordered source manifests used by replay's PIT provenance chain."""
        return (
            self.dataset_manifest,
            self.graph_manifest,
            self.universe_manifest,
        )

    @classmethod
    def from_doc(cls, doc: Mapping[str, Any]) -> "ExperimentSpec":
        payload = dict(doc)
        expected = payload.pop("fingerprint", None)
        expected_config_hash = payload.pop("effective_config_hash", None)
        # RethinkDB stores JSON numbers as doubles, so integer provenance can
        # round-trip as ``179.0``. Accept only exact integral round-trips here;
        # direct construction remains strict and rejects float inputs.
        for name in ("seed", "predeclared_repeats"):
            value = payload.get(name)
            if isinstance(value, float) and value.is_integer():
                payload[name] = int(value)
        spec = cls(**payload)
        if (
            expected_config_hash is not None
            and str(expected_config_hash) != spec.effective_config_hash
        ):
            raise ExperimentRegistryError(
                "stored effective configuration hash is invalid"
            )
        if expected is not None and str(expected) != spec.fingerprint:
            raise ExperimentRegistryError("stored experiment fingerprint is invalid")
        return spec


@dataclass(frozen=True)
class RegisteredExperiment:
    experiment_id: str
    search_scope: str
    fingerprint: str
    registered_at: datetime
    spec: ExperimentSpec
    status: str = field(default="registered", init=False)

    def __post_init__(self):
        if self.registered_at.tzinfo is None:
            raise ValueError("registered_at must be timezone-aware")
        if self.experiment_id != self.spec.experiment_id:
            raise ValueError("registration and spec experiment IDs differ")
        if self.search_scope != self.spec.search_scope:
            raise ValueError("registration and spec search scopes differ")
        if self.fingerprint != self.spec.fingerprint:
            raise ValueError("registration and spec fingerprints differ")

    def to_doc(self) -> dict:
        return {
            "id": self.experiment_id,
            "record_kind": "registration",
            "experiment_id": self.experiment_id,
            "search_scope": self.search_scope,
            "fingerprint": self.fingerprint,
            "registered_at": self.registered_at.astimezone(timezone.utc).isoformat(),
            "spec": self.spec.to_doc(),
        }

    @classmethod
    def from_doc(cls, doc: Mapping[str, Any]) -> "RegisteredExperiment":
        spec = ExperimentSpec.from_doc(doc["spec"])
        return cls(
            experiment_id=str(doc["experiment_id"]),
            search_scope=str(doc["search_scope"]),
            fingerprint=str(doc["fingerprint"]),
            registered_at=datetime.fromisoformat(str(doc["registered_at"])),
            spec=spec,
        )


@dataclass(frozen=True)
class ExperimentOutcome:
    experiment_id: str
    status: str
    recorded_at: datetime
    result: Mapping[str, Any]
    failure_reason: str | None = None

    def __post_init__(self):
        if self.status not in {"completed", "failed", "stopped"}:
            raise ValueError(f"invalid terminal experiment status {self.status!r}")
        if self.recorded_at.tzinfo is None:
            raise ValueError("recorded_at must be timezone-aware")
        if self.status == "completed" and self.failure_reason is not None:
            raise ValueError("completed experiments cannot have a failure reason")
        if self.status != "completed" and not str(self.failure_reason or "").strip():
            raise ValueError(f"{self.status} experiments require a reason")
        object.__setattr__(self, "result", FrozenDict(self.result or {}))

    def to_doc(self) -> dict:
        return {
            "id": f"{self.experiment_id}:terminal",
            "record_kind": "outcome",
            "experiment_id": self.experiment_id,
            "status": self.status,
            "recorded_at": self.recorded_at.astimezone(timezone.utc).isoformat(),
            "result": _thaw_json(self.result),
            "failure_reason": self.failure_reason,
        }

    @classmethod
    def from_doc(cls, doc: Mapping[str, Any]) -> "ExperimentOutcome":
        return cls(
            experiment_id=str(doc["experiment_id"]),
            status=str(doc["status"]),
            recorded_at=datetime.fromisoformat(str(doc["recorded_at"])),
            result=doc.get("result") or {},
            failure_reason=doc.get("failure_reason"),
        )


class InMemoryExperimentStore:
    """Append-only deterministic store used when no durable store is supplied."""

    def __init__(self):
        self._registrations: dict[str, RegisteredExperiment] = {}
        self._outcomes: dict[str, ExperimentOutcome] = {}

    def insert_experiment_registration(
        self, registration: RegisteredExperiment
    ) -> bool:
        if registration.experiment_id in self._registrations:
            return False
        self._registrations[registration.experiment_id] = registration
        return True

    def insert_experiment_outcome(self, outcome: ExperimentOutcome) -> bool:
        if outcome.experiment_id in self._outcomes:
            return False
        self._outcomes[outcome.experiment_id] = outcome
        return True

    def get_experiment_registration(
        self, experiment_id: str
    ) -> RegisteredExperiment | None:
        return self._registrations.get(str(experiment_id))

    def get_experiment_outcome(
        self, experiment_id: str
    ) -> ExperimentOutcome | None:
        return self._outcomes.get(str(experiment_id))

    def list_experiment_registrations(
        self, scope: str | None = None
    ) -> tuple[RegisteredExperiment, ...]:
        rows = tuple(self._registrations.values())
        if scope is None:
            return rows
        return tuple(row for row in rows if row.search_scope == str(scope))

    def experiment_trial_count(self, scope: str | None = None) -> int:
        return len(self.list_experiment_registrations(scope))


class ExperimentRegistry:
    """Append-only attempt registry over an in-memory or durable store."""

    def __init__(
        self,
        store=None,
        *,
        clock: Callable[[], datetime] | None = None,
    ):
        self._store = store or InMemoryExperimentStore()
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def _now(self) -> datetime:
        value = self._clock()
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise ValueError("registry clock must return a timezone-aware datetime")
        return value.astimezone(timezone.utc)

    def register_before_run(self, spec: ExperimentSpec) -> RegisteredExperiment:
        if not isinstance(spec, ExperimentSpec):
            raise TypeError("spec must be an ExperimentSpec")
        registered = RegisteredExperiment(
            experiment_id=spec.experiment_id,
            search_scope=spec.search_scope,
            fingerprint=spec.fingerprint,
            registered_at=self._now(),
            spec=spec,
        )
        if not self._store.insert_experiment_registration(registered):
            raise DuplicateExperimentError(
                f"experiment {spec.experiment_id!r} was already registered; "
                "a repeated attempt requires a new experiment_id"
            )
        return registered

    def _require_registration(self, experiment_id: str) -> RegisteredExperiment:
        registered = self._store.get_experiment_registration(str(experiment_id))
        if registered is None:
            raise KeyError(f"experiment {experiment_id!r} was never registered")
        return registered

    def _append_outcome(self, outcome: ExperimentOutcome) -> None:
        self._require_registration(outcome.experiment_id)
        if not self._store.insert_experiment_outcome(outcome):
            raise ExperimentAlreadyTerminalError(
                f"experiment {outcome.experiment_id!r} is already terminal"
            )

    def complete_experiment(
        self, experiment_id: str, result: Mapping[str, Any]
    ) -> None:
        if not isinstance(result, Mapping):
            raise TypeError("experiment result must be a mapping")
        self._append_outcome(
            ExperimentOutcome(
                experiment_id=str(experiment_id),
                status="completed",
                recorded_at=self._now(),
                result=result,
            )
        )

    def fail(self, experiment_id: str, reason: str) -> None:
        self._append_outcome(
            ExperimentOutcome(
                experiment_id=str(experiment_id),
                status="failed",
                recorded_at=self._now(),
                result={},
                failure_reason=_require_text("failure reason", reason),
            )
        )

    def stop(self, experiment_id: str, reason: str) -> None:
        self._append_outcome(
            ExperimentOutcome(
                experiment_id=str(experiment_id),
                status="stopped",
                recorded_at=self._now(),
                result={},
                failure_reason=_require_text("stop reason", reason),
            )
        )

    def outcome(self, experiment_id: str) -> ExperimentOutcome | None:
        self._require_registration(experiment_id)
        return self._store.get_experiment_outcome(str(experiment_id))

    def status(self, experiment_id: str) -> str:
        terminal = self.outcome(experiment_id)
        return terminal.status if terminal is not None else "registered"

    def all_experiments(
        self, scope: str | None = None
    ) -> tuple[RegisteredExperiment, ...]:
        return self._store.list_experiment_registrations(scope)

    def trial_count(self, *, scope: str | None = None) -> int:
        return int(self._store.experiment_trial_count(scope))
