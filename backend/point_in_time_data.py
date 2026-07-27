"""Immutable point-in-time dataset and availability contracts.

Research callers use :class:`PointInTimeContext` as the boundary between data
that may have been prefetched and data that was actually available to a
historical decision.  The module is deliberately storage-agnostic: callers
provide the availability timestamp for their own record type.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from types import MappingProxyType
from typing import Any, Callable, Iterable, Mapping, TypeVar


UTC = timezone.utc
_RecordT = TypeVar("_RecordT")


class PointInTimeDataError(RuntimeError):
    """Raised when a strict historical input cannot be proven point-in-time."""


def require_aware_utc(value: Any, *, field: str) -> datetime:
    """Return ``value`` as an aware UTC datetime or raise a typed error."""

    parsed = value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            raise PointInTimeDataError(f"{field}: availability timestamp is missing")
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError as exc:
            raise PointInTimeDataError(
                f"{field}: availability timestamp is invalid"
            ) from exc
    if not isinstance(parsed, datetime):
        raise PointInTimeDataError(f"{field}: availability timestamp is missing")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise PointInTimeDataError(f"{field}: timestamp must be timezone-aware")
    return parsed.astimezone(UTC)


def _iso_z(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _cache_component(value: Any) -> str:
    if isinstance(value, datetime):
        return _iso_z(require_aware_utc(value, field="cache key"))
    if isinstance(value, str):
        return value
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


@dataclass(frozen=True, slots=True)
class DatasetManifest:
    """Identity and source hashes for one immutable research dataset."""

    manifest_id: str
    source_hashes: Mapping[str, str]
    created_at: datetime

    def __post_init__(self) -> None:
        manifest_id = str(self.manifest_id or "").strip()
        if not manifest_id:
            raise PointInTimeDataError("manifest_id is required")
        if not isinstance(self.source_hashes, Mapping):
            raise PointInTimeDataError("source_hashes must be a mapping")
        normalized: dict[str, str] = {}
        for raw_name, raw_hash in self.source_hashes.items():
            name = str(raw_name or "").strip()
            source_hash = str(raw_hash or "").strip()
            if not name or not source_hash:
                raise PointInTimeDataError(
                    "source_hashes keys and values must be non-empty"
                )
            normalized[name] = source_hash
        object.__setattr__(self, "manifest_id", manifest_id)
        object.__setattr__(
            self,
            "source_hashes",
            MappingProxyType(dict(sorted(normalized.items()))),
        )
        object.__setattr__(
            self,
            "created_at",
            require_aware_utc(self.created_at, field="created_at"),
        )

    def __hash__(self) -> int:
        return hash(
            (
                self.manifest_id,
                tuple(self.source_hashes.items()),
                self.created_at,
            )
        )


@dataclass(frozen=True, slots=True)
class PointInTimeContext:
    """The latest timestamp and immutable manifest visible to a decision."""

    as_of: datetime
    manifest: DatasetManifest
    strict: bool = True
    is_live: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.manifest, DatasetManifest):
            raise PointInTimeDataError("manifest must be a DatasetManifest")
        object.__setattr__(
            self,
            "as_of",
            require_aware_utc(self.as_of, field="as_of"),
        )
        object.__setattr__(self, "strict", bool(self.strict))
        object.__setattr__(self, "is_live", bool(self.is_live))

    @classmethod
    def for_live(
        cls,
        *,
        as_of: datetime,
        manifest: DatasetManifest,
    ) -> "PointInTimeContext":
        """Create the explicit context that permits current-state live inputs."""

        return cls(as_of=as_of, manifest=manifest, strict=False, is_live=True)

    def cache_key(self, namespace: str, *parts: Any) -> tuple[str, ...]:
        """Return a derived-cache key bound to dataset identity and decision time."""

        prefix = str(namespace or "").strip()
        if not prefix:
            raise PointInTimeDataError("cache namespace is required")
        return (
            prefix,
            self.manifest.manifest_id,
            _iso_z(self.as_of),
            *(_cache_component(part) for part in parts),
        )


def filter_available(
    records: Iterable[_RecordT],
    *,
    context: PointInTimeContext,
    available_at: Callable[[_RecordT], Any],
) -> tuple[_RecordT, ...]:
    """Return records whose availability is no later than ``context.as_of``.

    A strict context refuses records without a provable aware availability
    timestamp.  A non-strict live context ignores malformed records instead of
    guessing their publication time.
    """

    if not isinstance(context, PointInTimeContext):
        raise PointInTimeDataError("context must be a PointInTimeContext")
    if not callable(available_at):
        raise PointInTimeDataError("available_at must be callable")

    visible: list[_RecordT] = []
    for index, record in enumerate(records or ()):
        try:
            raw_available_at = available_at(record)
            record_available_at = require_aware_utc(
                raw_available_at,
                field=f"record[{index}] availability timestamp",
            )
        except PointInTimeDataError:
            if context.strict:
                raise
            continue
        except Exception as exc:
            if context.strict:
                raise PointInTimeDataError(
                    f"record[{index}] availability timestamp could not be read"
                ) from exc
            continue
        if record_available_at <= context.as_of:
            visible.append(record)
    return tuple(visible)


def _snapshot_payload(record: Any) -> Any:
    if not isinstance(record, Mapping):
        return record
    for key in ("payload", "data", "snapshot"):
        if key in record:
            return record[key]
    metadata = {
        "available_at",
        "effective_at",
        "as_of",
        "snapshot_at",
        "manifest_id",
    }
    return {key: value for key, value in record.items() if key not in metadata}


def load_snapshot_payload(
    store: Any,
    *,
    dataset: str,
    context: PointInTimeContext,
) -> Any:
    """Load the latest eligible manifest-bound snapshot from an in-memory store.

    The storage-neutral mapping form is ``{dataset: [snapshot, ...]}``, where
    each historical snapshot declares ``manifest_id``, ``effective_at``,
    ``available_at``, and ``payload``.  Live contexts may instead use
    ``{dataset: {"current": value}}``.  Historical contexts never inspect that
    current-state entry.
    """

    dataset_name = str(dataset or "").strip()
    if not dataset_name:
        raise PointInTimeDataError("snapshot dataset is required")
    if not isinstance(context, PointInTimeContext):
        raise PointInTimeDataError("context must be a PointInTimeContext")

    bucket: Any = None
    if isinstance(store, Mapping):
        bucket = store.get(dataset_name)
    elif store is not None:
        loader = getattr(store, "load_snapshot", None)
        if callable(loader):
            bucket = loader(dataset=dataset_name, context=context)

    if context.is_live:
        current: Any = None
        if isinstance(bucket, Mapping) and "current" in bucket:
            current = bucket["current"]
        elif store is not None:
            current_loader = getattr(store, "load_current_snapshot", None)
            if callable(current_loader):
                current = current_loader(dataset=dataset_name)
        if current is None:
            raise PointInTimeDataError(
                f"{dataset_name} snapshot is missing for explicit live context"
            )
        return _snapshot_payload(current)

    if isinstance(bucket, Mapping):
        if "snapshots" in bucket:
            candidates = bucket.get("snapshots") or ()
        elif any(
            key in bucket
            for key in ("manifest_id", "effective_at", "as_of", "snapshot_at")
        ):
            candidates = (bucket,)
        else:
            candidates = ()
    elif isinstance(bucket, Iterable) and not isinstance(bucket, (str, bytes)):
        candidates = bucket
    else:
        candidates = ()

    eligible: list[tuple[datetime, Any]] = []
    for index, record in enumerate(candidates):
        if not isinstance(record, Mapping):
            if context.strict:
                raise PointInTimeDataError(
                    f"{dataset_name} snapshot[{index}] metadata is missing"
                )
            continue
        if str(record.get("manifest_id") or "").strip() != (
            context.manifest.manifest_id
        ):
            continue
        effective_raw = (
            record.get("effective_at")
            or record.get("as_of")
            or record.get("snapshot_at")
        )
        available_raw = record.get("available_at")
        try:
            effective_at = require_aware_utc(
                effective_raw,
                field=f"{dataset_name} snapshot[{index}] effective_at",
            )
            available_at = require_aware_utc(
                available_raw,
                field=f"{dataset_name} snapshot[{index}] available_at",
            )
        except PointInTimeDataError:
            if context.strict:
                raise
            continue
        if effective_at > context.as_of or available_at > context.as_of:
            continue
        payload = _snapshot_payload(record)
        if payload is None:
            continue
        eligible.append((effective_at, payload))

    if not eligible:
        raise PointInTimeDataError(
            f"{dataset_name} snapshot is missing for manifest "
            f"{context.manifest.manifest_id} at {_iso_z(context.as_of)}"
        )
    eligible.sort(key=lambda item: item[0])
    return eligible[-1][1]


__all__ = [
    "DatasetManifest",
    "PointInTimeContext",
    "PointInTimeDataError",
    "filter_available",
    "load_snapshot_payload",
    "require_aware_utc",
]
