"""Immutable point-in-time dataset and availability contracts.

Research callers use :class:`PointInTimeContext` as the boundary between data
that may have been prefetched and data that was actually available to a
historical decision.  The module is deliberately storage-agnostic: callers
provide the availability timestamp for their own record type.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
import json
from types import MappingProxyType
from typing import Any, Callable, Iterable, Iterator, Mapping, TypeVar


UTC = timezone.utc
_RecordT = TypeVar("_RecordT")


class PointInTimeDataError(RuntimeError):
    """Raised when a strict historical input cannot be proven point-in-time."""


class _FrozenDict(dict):
    """``dict``-compatible recursively frozen mapping used by snapshot payloads."""

    @staticmethod
    def _immutable(*args, **kwargs):
        raise TypeError("immutable snapshot mappings cannot be modified")

    __setitem__ = _immutable
    __delitem__ = _immutable
    clear = _immutable
    pop = _immutable
    popitem = _immutable
    setdefault = _immutable
    update = _immutable
    __ior__ = _immutable


def _freeze_snapshot_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return _FrozenDict(
            {
                key: _freeze_snapshot_value(item)
                for key, item in value.items()
            }
        )
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_snapshot_value(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(_freeze_snapshot_value(item) for item in value)
    return value


class ImmutableSnapshotStore(Mapping[str, Any]):
    """Owned, recursively frozen copy of dated research snapshots.

    Provider handles such as an already-open graph driver remain opaque
    objects; all mapping/sequence structure around them is copied and frozen.
    This prevents a prefetched source mapping from being mutated after a
    historical run has started.
    """

    __slots__ = ("_datasets",)

    def __init__(self, snapshots: Mapping[str, Any]):
        if not isinstance(snapshots, Mapping):
            raise PointInTimeDataError(
                "point-in-time snapshot store must be a mapping"
            )
        frozen = _freeze_snapshot_value(snapshots)
        if not isinstance(frozen, Mapping):
            raise PointInTimeDataError(
                "point-in-time snapshot store could not be frozen"
            )
        self._datasets = frozen

    @classmethod
    def coerce(cls, snapshots: Any) -> "ImmutableSnapshotStore":
        if isinstance(snapshots, cls):
            return snapshots
        return cls(snapshots)

    def __getitem__(self, key: str) -> Any:
        return self._datasets[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._datasets)

    def __len__(self) -> int:
        return len(self._datasets)


def coerce_dataset_manifest(value: Any) -> "DatasetManifest":
    """Return a validated manifest from a manifest object or stored mapping."""

    if isinstance(value, DatasetManifest):
        return value
    if not isinstance(value, Mapping):
        raise PointInTimeDataError(
            "point-in-time manifest is required for historical Graph Nexus"
        )
    return DatasetManifest(
        manifest_id=value.get("manifest_id"),
        source_hashes=value.get("source_hashes"),
        created_at=value.get("created_at"),
    )


def resolve_nyse_session_close(session_date: date) -> datetime:
    """Resolve an exact NYSE regular-session close without network I/O.

    Strict historical execution does not use the weekday approximation from
    the live calendar fallback because it cannot prove holidays or early
    closes. Missing calendar dependencies therefore fail closed.
    """

    if isinstance(session_date, datetime):
        session_date = session_date.date()
    if not isinstance(session_date, date):
        raise PointInTimeDataError("session close requires a session date")
    try:
        import exchange_calendars as exchange_calendars
        import pandas as pandas
    except Exception as exc:
        raise PointInTimeDataError(
            "strict historical session calendar is unavailable"
        ) from exc
    try:
        calendar = exchange_calendars.get_calendar("XNYS")
        session = pandas.Timestamp(session_date)
        if not bool(calendar.is_session(session)):
            raise PointInTimeDataError(
                f"{session_date.isoformat()} is not an NYSE trading session"
            )
        close = calendar.session_close(session)
        if hasattr(close, "to_pydatetime"):
            close = close.to_pydatetime()
        return require_aware_utc(close, field="NYSE session close")
    except PointInTimeDataError:
        raise
    except Exception as exc:
        raise PointInTimeDataError(
            f"NYSE session close is unavailable for {session_date.isoformat()}"
        ) from exc


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
    "ImmutableSnapshotStore",
    "PointInTimeContext",
    "PointInTimeDataError",
    "coerce_dataset_manifest",
    "filter_available",
    "load_snapshot_payload",
    "require_aware_utc",
    "resolve_nyse_session_close",
]
