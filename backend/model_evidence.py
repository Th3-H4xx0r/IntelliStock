"""Deterministic, immutable evidence records for model calls in backtests.

This module deliberately has no provider, cache, database, or strategy
dependencies.  Callers supply complete request and occurrence identities, then
use a session to record or replay immutable rows.
"""
from __future__ import annotations

import base64
import copy
import dataclasses
import datetime as dt
import hashlib
import json
import math
import re
import threading
from collections.abc import Iterable, Mapping
from types import MappingProxyType
from typing import Any


_SECRET_KEY = re.compile(
    r"(?:api[_-]?key|secret|password|credential|authorization|bearer|"
    r"private[_-]?key|access[_-]?key|(?:^|[_-])token(?:$|[_-]))",
    re.IGNORECASE,
)
_LEDGER_VERSION = "model-evidence-ledger-v1"
_SESSION_MODES = frozenset({"off", "record", "record_extend", "replay"})


class ModelEvidenceError(ValueError):
    """Raised when evidence would be unsafe, ambiguous, or incomplete."""


def _reject_secrets(value: Any, *, path: str = "request") -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if not isinstance(key, str):
                raise ModelEvidenceError(f"{path} keys must be strings")
            # `max_tokens` is a normal generation setting, not a credential.
            if key.casefold() != "max_tokens" and _SECRET_KEY.search(key):
                raise ModelEvidenceError(f"secret-bearing key is not allowed: {path}.{key}")
            _reject_secrets(nested, path=f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, nested in enumerate(value):
            _reject_secrets(nested, path=f"{path}[{index}]")


def _json_ready(value: Any) -> Any:
    """Return a JSON-safe deep copy with explicit representations for bytes/time."""
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ModelEvidenceError("non-finite numbers are not canonical evidence values")
        return value
    if isinstance(value, bytes):
        return {"__bytes_b64__": base64.b64encode(value).decode("ascii")}
    if isinstance(value, (dt.datetime, dt.date, dt.time)):
        return {"__iso8601__": value.isoformat()}
    if dataclasses.is_dataclass(value):
        return _json_ready(dataclasses.asdict(value))
    if isinstance(value, Mapping):
        normalized: dict[str, Any] = {}
        for key, nested in value.items():
            if not isinstance(key, str):
                raise ModelEvidenceError("evidence mapping keys must be strings")
            normalized[key] = _json_ready(nested)
        return normalized
    if isinstance(value, (list, tuple)):
        return [_json_ready(item) for item in value]
    raise ModelEvidenceError(f"unsupported evidence value type: {type(value).__name__}")


def _canonical_bytes(value: Any) -> bytes:
    _reject_secrets(value)
    return json.dumps(
        _json_ready(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(nested) for key, nested in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, tuple):
        return tuple(_freeze(item) for item in value)
    return value


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw(nested) for key, nested in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return copy.deepcopy(value)


def _non_empty_string(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ModelEvidenceError(f"{name} must be a non-empty string")
    return value


def _schema_bytes(schema: Any) -> bytes:
    if schema is None:
        return b""
    if isinstance(schema, bytes):
        return schema
    if isinstance(schema, str):
        return schema.encode("utf-8")
    return _canonical_bytes(schema)


def canonical_request_envelope(
    *,
    requested_provider: str,
    requested_model: str,
    adapter_identity: str,
    prompt: Any,
    system_prompt: Any,
    schema: Any = None,
    tools: Any = None,
    tool_choice: Any = None,
    generation_settings: Any = None,
    fallback_policy: Any = None,
    canonicalization_version: str = "model-evidence-v1",
) -> dict[str, Any]:
    """Build the complete, credential-free identity for a model request.

    Response facts intentionally do not belong here: they are stored on
    :class:`ModelEvidenceRecord` so a fallback response cannot alter lookup
    identity.
    """
    envelope = {
        "canonicalization_version": _non_empty_string(
            canonicalization_version, "canonicalization_version"
        ),
        "requested_provider": _non_empty_string(requested_provider, "requested_provider"),
        "requested_model": _non_empty_string(requested_model, "requested_model"),
        "adapter_identity": _non_empty_string(adapter_identity, "adapter_identity"),
        "prompt": _json_ready(prompt),
        "system_prompt": _json_ready(system_prompt),
        "schema_bytes": base64.b64encode(_schema_bytes(schema)).decode("ascii"),
        "tools": _json_ready(tools),
        "tool_choice": _json_ready(tool_choice),
        "generation_settings": _json_ready(generation_settings or {}),
        "fallback_policy": _json_ready(fallback_policy or {}),
    }
    _reject_secrets(envelope)
    # Round-trip through canonical JSON makes key order and mutable inputs inert.
    return json.loads(_canonical_bytes(envelope).decode("utf-8"))


@dataclasses.dataclass(frozen=True)
class ModelEvidenceContext:
    """Caller-owned deterministic identity for one logical request occurrence."""

    decision_at: dt.datetime | str
    call_site: str
    role: str
    subject: str
    local_sequence: int | str

    def __post_init__(self) -> None:
        if self.decision_at is None or (isinstance(self.decision_at, str) and not self.decision_at.strip()):
            raise ModelEvidenceError("decision_at is required")
        if not isinstance(self.decision_at, (dt.datetime, str)):
            raise ModelEvidenceError("decision_at must be a timestamp string or datetime")
        _non_empty_string(self.call_site, "call_site")
        _non_empty_string(self.role, "role")
        _non_empty_string(self.subject, "subject")
        if isinstance(self.local_sequence, bool) or not isinstance(self.local_sequence, (int, str)):
            raise ModelEvidenceError("local_sequence must be deterministic text or an integer")
        if isinstance(self.local_sequence, int) and self.local_sequence < 0:
            raise ModelEvidenceError("local_sequence must be non-negative")
        if isinstance(self.local_sequence, str) and not self.local_sequence.strip():
            raise ModelEvidenceError("local_sequence must be non-empty")

    def canonical_value(self) -> dict[str, Any]:
        timestamp = self.decision_at.isoformat() if isinstance(self.decision_at, dt.datetime) else self.decision_at
        return {
            "decision_at": timestamp,
            "call_site": self.call_site,
            "role": self.role,
            "subject": self.subject,
            "local_sequence": self.local_sequence,
        }

    @property
    def occurrence_key(self) -> str:
        return _digest({"occurrence": self.canonical_value()})


def semantic_request_id(envelope: Mapping[str, Any], *, context: ModelEvidenceContext) -> str:
    """Return a request ID that is stable across worker scheduling/order."""
    if not isinstance(context, ModelEvidenceContext):
        raise ModelEvidenceError("context must be a ModelEvidenceContext")
    canonical_envelope = json.loads(_canonical_bytes(envelope).decode("utf-8"))
    return _digest(
        {
            "canonicalization_version": canonical_envelope.get("canonicalization_version"),
            "request": canonical_envelope,
            "occurrence_key": context.occurrence_key,
        }
    )


@dataclasses.dataclass(frozen=True)
class ModelEvidenceRecord:
    """One immutable request/response row, addressed by its semantic request ID."""

    semantic_id: str
    envelope: Mapping[str, Any]
    outcome: Any
    response_metadata: Mapping[str, Any]

    def __post_init__(self) -> None:
        if not isinstance(self.semantic_id, str) or not re.fullmatch(r"[0-9a-f]{64}", self.semantic_id):
            raise ModelEvidenceError("semantic_id must be a SHA-256 hex digest")
        canonical_envelope = json.loads(_canonical_bytes(self.envelope).decode("utf-8"))
        canonical_outcome = _json_ready(self.outcome)
        canonical_metadata = json.loads(_canonical_bytes(self.response_metadata).decode("utf-8"))
        object.__setattr__(self, "envelope", _freeze(canonical_envelope))
        object.__setattr__(self, "outcome", _freeze(canonical_outcome))
        object.__setattr__(self, "response_metadata", _freeze(canonical_metadata))

    @classmethod
    def from_response(
        cls,
        *,
        semantic_id: str,
        envelope: Mapping[str, Any],
        outcome: Any,
        attempted_models: Iterable[str] = (),
        effective_model: str | None = None,
        raw_response: Any = None,
        fallback_state: str = "not_used",
        response_metadata: Mapping[str, Any] | None = None,
    ) -> "ModelEvidenceRecord":
        metadata = dict(response_metadata or {})
        metadata.update(
            {
                "attempted_models": list(attempted_models),
                "effective_model": effective_model,
                "raw_response_hash": _digest(raw_response),
                "validated_response_hash": _digest(outcome),
                "fallback_state": _non_empty_string(fallback_state, "fallback_state"),
                "successful": True,
                "outcome_is_none": outcome is None,
            }
        )
        return cls(
            semantic_id=semantic_id,
            envelope=envelope,
            outcome=outcome,
            response_metadata=metadata,
        )

    def canonical_value(self) -> dict[str, Any]:
        return {
            "semantic_id": self.semantic_id,
            "envelope": _thaw(self.envelope),
            "outcome": _thaw(self.outcome),
            "response_metadata": _thaw(self.response_metadata),
        }

    @property
    def content_hash(self) -> str:
        return _digest(self.canonical_value())


class ModelEvidenceLedger:
    """Thread-safe immutable semantic-ID to response-row mapping."""

    def __init__(self, records: Iterable[ModelEvidenceRecord] = ()) -> None:
        self._lock = threading.RLock()
        self._records: dict[str, ModelEvidenceRecord] = {}
        self._conflicts: set[str] = set()
        for record in records:
            self.publish(record)

    def publish(self, record: ModelEvidenceRecord) -> ModelEvidenceRecord:
        if not isinstance(record, ModelEvidenceRecord):
            raise ModelEvidenceError("only ModelEvidenceRecord rows may be published")
        with self._lock:
            existing = self._records.get(record.semantic_id)
            if existing is None:
                self._records[record.semantic_id] = record
                return record
            if existing.content_hash == record.content_hash:
                return existing
            self._conflicts.add(record.semantic_id)
            raise ModelEvidenceError(f"divergent immutable row for semantic ID {record.semantic_id}")

    def get(self, semantic_id: str) -> ModelEvidenceRecord | None:
        with self._lock:
            return self._records.get(semantic_id)

    @property
    def records(self) -> tuple[ModelEvidenceRecord, ...]:
        with self._lock:
            return tuple(self._records[key] for key in sorted(self._records))

    @property
    def content_hash(self) -> str:
        return _digest({"ledger_version": _LEDGER_VERSION, "records": [row.canonical_value() for row in self.records]})

    def finalize(self) -> dict[str, Any]:
        with self._lock:
            if self._conflicts:
                raise ModelEvidenceError("cannot finalize a ledger with divergent immutable rows")
        return self.export()

    def export(self) -> dict[str, Any]:
        payload = {
            "ledger_version": _LEDGER_VERSION,
            "records": [row.canonical_value() for row in self.records],
        }
        payload["content_hash"] = _digest(payload)
        return payload

    @classmethod
    def from_export(cls, payload: Mapping[str, Any]) -> "ModelEvidenceLedger":
        if not isinstance(payload, Mapping):
            raise ModelEvidenceError("ledger export must be a mapping")
        actual_hash = payload.get("content_hash")
        unsigned = {key: value for key, value in payload.items() if key != "content_hash"}
        if not isinstance(actual_hash, str) or _digest(unsigned) != actual_hash:
            raise ModelEvidenceError("ledger content hash does not match its canonical contents")
        if unsigned.get("ledger_version") != _LEDGER_VERSION:
            raise ModelEvidenceError("unsupported ledger version")
        rows = unsigned.get("records")
        if not isinstance(rows, list):
            raise ModelEvidenceError("ledger records must be a list")
        records = []
        for row in rows:
            if not isinstance(row, Mapping):
                raise ModelEvidenceError("ledger row must be a mapping")
            try:
                records.append(
                    ModelEvidenceRecord(
                        semantic_id=row["semantic_id"],
                        envelope=row["envelope"],
                        outcome=row["outcome"],
                        response_metadata=row["response_metadata"],
                    )
                )
            except KeyError as exc:
                raise ModelEvidenceError(f"ledger row is missing {exc.args[0]}") from exc
        ledger = cls(records)
        if ledger.content_hash != _digest(unsigned):
            raise ModelEvidenceError("ledger rows are not canonical")
        return ledger


class ModelEvidenceSession:
    """Arm-scoped consumption accounting over an immutable evidence ledger."""

    def __init__(
        self,
        *,
        mode: str,
        ledger: ModelEvidenceLedger | None = None,
        arm_id: str | None = None,
        declared_occurrences: Iterable[str] | None = None,
    ) -> None:
        if mode not in _SESSION_MODES:
            raise ModelEvidenceError(f"unsupported evidence mode: {mode}")
        if mode != "off" and not _non_empty_string(arm_id, "arm_id"):
            raise ModelEvidenceError("arm_id is required when evidence is enabled")
        self.mode = mode
        self.ledger = ledger or ModelEvidenceLedger()
        self.arm_id = arm_id
        self._declared = self._normalize_occurrences(declared_occurrences)
        self._consumed: set[str] = set()
        self._replayed: set[str] = set()
        self._recorded: set[str] = set()
        self._lock = threading.RLock()

    @staticmethod
    def _normalize_occurrences(occurrences: Iterable[str] | None) -> set[str] | None:
        if occurrences is None:
            return None
        supplied = tuple(occurrences)
        normalized = set(supplied)
        if len(normalized) != len(supplied):
            raise ModelEvidenceError("declared occurrence IDs must be unique")
        if any(not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value) for value in normalized):
            raise ModelEvidenceError("declared occurrences must be semantic SHA-256 IDs")
        return normalized

    def replay(self, semantic_id: str) -> Any:
        if self.mode == "off":
            return None
        with self._lock:
            record = self.ledger.get(semantic_id)
            if record is None:
                raise ModelEvidenceError(f"replay miss for semantic ID {semantic_id}")
            if self._declared is not None and semantic_id not in self._declared:
                raise ModelEvidenceError(f"semantic ID {semantic_id} is not declared for arm {self.arm_id}")
            if semantic_id in self._consumed:
                raise ModelEvidenceError(f"over-consumption of semantic ID {semantic_id}")
            self._consumed.add(semantic_id)
            self._replayed.add(semantic_id)
            return _thaw(record.outcome)

    def record(self, record: ModelEvidenceRecord) -> Any:
        if self.mode == "off":
            return _thaw(record.outcome)
        if self.mode == "replay":
            raise ModelEvidenceError("cannot publish a row in replay mode")
        with self._lock:
            if self._declared is not None and record.semantic_id not in self._declared:
                raise ModelEvidenceError(f"semantic ID {record.semantic_id} is not declared for arm {self.arm_id}")
            self.ledger.publish(record)
            self._recorded.add(record.semantic_id)
            return _thaw(record.outcome)

    def resolve(self, record: ModelEvidenceRecord) -> Any:
        """Replay if available; otherwise publish only in recording modes."""
        if self.mode == "replay":
            return self.replay(record.semantic_id)
        if self.mode == "record_extend" and self.ledger.get(record.semantic_id) is not None:
            self.ledger.publish(record)  # verify identical before consuming the union row
            return self.replay(record.semantic_id)
        return self.record(record)

    def finalize(self) -> dict[str, Any]:
        if self.mode == "off":
            return {"mode": "off"}
        with self._lock:
            observed = self._consumed | self._recorded
            if self._declared is not None:
                missing = self._declared - observed
                extra = observed - self._declared
                if missing:
                    raise ModelEvidenceError(f"unused declared occurrences for arm {self.arm_id}: {sorted(missing)}")
                if extra:
                    raise ModelEvidenceError(f"undeclared occurrences for arm {self.arm_id}: {sorted(extra)}")
            self.ledger.finalize()
            return {
                "mode": self.mode,
                "arm_id": self.arm_id,
                "declared_occurrences": sorted(self._declared if self._declared is not None else observed),
                "consumed_occurrences": sorted(self._consumed),
                "replayed_occurrences": sorted(self._replayed),
                "recorded_occurrences": sorted(self._recorded),
                "ledger_content_hash": self.ledger.content_hash,
            }


_session_lock = threading.RLock()
_active_session: ModelEvidenceSession | None = None


def activate_model_evidence_session(session: ModelEvidenceSession | None) -> ModelEvidenceSession | None:
    """Atomically install the single backtest-owned process-global session."""
    if session is not None and not isinstance(session, ModelEvidenceSession):
        raise ModelEvidenceError("active session must be a ModelEvidenceSession or None")
    global _active_session
    with _session_lock:
        _active_session = session
        return session


def get_model_evidence_session() -> ModelEvidenceSession | None:
    """Return the shared session for worker threads without copying it."""
    with _session_lock:
        return _active_session


def clear_model_evidence_session() -> None:
    """Clear the process-global session at the enclosing backtest boundary."""
    global _active_session
    with _session_lock:
        _active_session = None
