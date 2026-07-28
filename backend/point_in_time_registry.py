"""Append-only point-in-time dataset registry.

Snapshots are content-addressed by SHA-256.  A finalized manifest references
one immutable hash for every required Graph Nexus input and is published only
after all snapshot rows have been verified.  Historical readers re-hash every
payload before returning it.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, dataclass, is_dataclass
from datetime import date, datetime
from enum import Enum
import hashlib
import json
import math
import os
from types import MappingProxyType
from typing import Any, Mapping

from point_in_time_data import (
    DatasetManifest,
    PointInTimeContext,
    PointInTimeDataError,
    require_aware_utc,
)


REQUIRED_DATASETS = ("graph", "fundamentals", "universe", "news")
MANIFEST_TABLE = "PointInTimeManifests"
SNAPSHOT_TABLE = "PointInTimeDatasetSnapshots"
_SECRET_KEY_PARTS = (
    "api_key",
    "apikey",
    "secret",
    "password",
    "access_token",
    "refresh_token",
    "private_key",
    "credential",
)


def _iso_z(value: datetime) -> str:
    return require_aware_utc(value, field="point-in-time timestamp").isoformat().replace(
        "+00:00", "Z"
    )


def canonical_payload(value: Any) -> Any:
    """Return a deterministic JSON-safe representation or fail closed."""

    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise PointInTimeDataError(
                "point-in-time payload contains a non-finite number"
            )
        return value
    if isinstance(value, datetime):
        return _iso_z(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Enum):
        return canonical_payload(value.value)
    if is_dataclass(value) and not isinstance(value, type):
        return canonical_payload(asdict(value))
    if isinstance(value, Mapping):
        normalized: dict[str, Any] = {}
        for raw_key, item in value.items():
            key = str(raw_key)
            if key in normalized:
                raise PointInTimeDataError(
                    "point-in-time payload has duplicate normalized keys"
                )
            normalized[key] = canonical_payload(item)
        return dict(sorted(normalized.items()))
    if isinstance(value, (list, tuple)):
        return [canonical_payload(item) for item in value]
    if isinstance(value, (set, frozenset)):
        normalized = [canonical_payload(item) for item in value]
        return sorted(
            normalized,
            key=lambda item: json.dumps(
                item,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ),
        )
    items = getattr(value, "items", None)
    if callable(items):
        try:
            return canonical_payload(dict(items()))
        except PointInTimeDataError:
            raise
        except Exception as exc:
            raise PointInTimeDataError(
                "point-in-time mapping-like payload could not be read"
            ) from exc
    iso_format = getattr(value, "iso_format", None)
    if callable(iso_format):
        return str(iso_format())
    raise PointInTimeDataError(
        f"point-in-time payload contains unsupported type {type(value).__name__}"
    )


def _canonical_json(value: Any) -> str:
    return json.dumps(
        canonical_payload(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def content_hash(value: Any) -> str:
    return "sha256:" + hashlib.sha256(
        _canonical_json(value).encode("utf-8")
    ).hexdigest()


def _snapshot_hash(dataset: str, payload: Any) -> str:
    return content_hash({"dataset": dataset, "payload": payload})


def _snapshot_rows_match(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    immutable_keys = ("id", "content_hash", "dataset", "payload", "record_version")
    return all(
        canonical_payload(left.get(key)) == canonical_payload(right.get(key))
        for key in immutable_keys
    )


def _reject_secret_keys(value: Any, *, path: str = "payload") -> None:
    if isinstance(value, Mapping):
        for raw_key, item in value.items():
            key = str(raw_key).strip().lower()
            if any(part in key for part in _SECRET_KEY_PARTS):
                raise PointInTimeDataError(
                    f"{path}: secret-bearing key is forbidden"
                )
            _reject_secret_keys(item, path=f"{path}.{raw_key}")
    elif isinstance(value, (list, tuple, set, frozenset)):
        for index, item in enumerate(value):
            _reject_secret_keys(item, path=f"{path}[{index}]")


@dataclass(frozen=True, slots=True)
class StoredManifest:
    manifest_id: str
    as_of: datetime
    created_at: datetime
    source_hashes: Mapping[str, str]
    code_revision: str
    status: str = "finalized"
    provenance: str = "strict_verified"

    def __post_init__(self):
        manifest_id = str(self.manifest_id or "").strip()
        if not manifest_id.startswith("pit-"):
            raise PointInTimeDataError("stored PIT manifest_id is invalid")
        status = str(self.status or "").strip()
        provenance = str(self.provenance or "").strip()
        if status != "finalized":
            raise PointInTimeDataError("PIT manifest is not finalized")
        if provenance != "strict_verified":
            raise PointInTimeDataError("PIT manifest provenance is not verified")
        hashes = {
            str(name): str(source_hash)
            for name, source_hash in dict(self.source_hashes or {}).items()
        }
        missing = sorted(set(REQUIRED_DATASETS) - set(hashes))
        if missing:
            raise PointInTimeDataError(
                "PIT manifest is missing required datasets: "
                + ", ".join(missing)
            )
        for name, source_hash in hashes.items():
            if not name or not source_hash.startswith("sha256:"):
                raise PointInTimeDataError(
                    "PIT manifest contains an invalid source hash"
                )
        object.__setattr__(self, "manifest_id", manifest_id)
        object.__setattr__(
            self, "as_of", require_aware_utc(self.as_of, field="manifest as_of")
        )
        object.__setattr__(
            self,
            "created_at",
            require_aware_utc(self.created_at, field="manifest created_at"),
        )
        object.__setattr__(
            self,
            "source_hashes",
            MappingProxyType(dict(sorted(hashes.items()))),
        )
        object.__setattr__(self, "code_revision", str(self.code_revision or "").strip())
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "provenance", provenance)

    def to_doc(self) -> dict[str, Any]:
        return {
            "id": self.manifest_id,
            "manifest_id": self.manifest_id,
            "as_of": _iso_z(self.as_of),
            "created_at": _iso_z(self.created_at),
            "source_hashes": dict(self.source_hashes),
            "code_revision": self.code_revision,
            "status": self.status,
            "provenance": self.provenance,
            "record_version": 1,
        }

    @classmethod
    def from_doc(cls, doc: Mapping[str, Any]) -> "StoredManifest":
        if not isinstance(doc, Mapping):
            raise PointInTimeDataError("stored PIT manifest row is missing")
        return cls(
            manifest_id=doc.get("manifest_id") or doc.get("id"),
            as_of=doc.get("as_of"),
            created_at=doc.get("created_at"),
            source_hashes=doc.get("source_hashes"),
            code_revision=doc.get("code_revision") or "",
            status=doc.get("status") or "",
            provenance=doc.get("provenance") or "",
        )


@dataclass(frozen=True, slots=True)
class ResolvedPointInTimeBundle:
    record: StoredManifest
    manifest: DatasetManifest
    context: PointInTimeContext
    store: Any


def _build_bundle_rows(
    *,
    as_of: datetime,
    datasets: Mapping[str, Any],
    code_revision: str,
    created_at: datetime | None,
) -> tuple[StoredManifest, dict[str, dict[str, Any]]]:
    as_of_utc = require_aware_utc(as_of, field="bundle as_of")
    if not isinstance(datasets, Mapping):
        raise PointInTimeDataError("PIT datasets must be a mapping")
    missing = sorted(set(REQUIRED_DATASETS) - set(datasets))
    if missing:
        raise PointInTimeDataError(
            "PIT bundle is missing required datasets: " + ", ".join(missing)
        )
    extra = sorted(set(datasets) - set(REQUIRED_DATASETS))
    if extra:
        raise PointInTimeDataError(
            "PIT bundle contains unsupported datasets: " + ", ".join(extra)
        )

    snapshots: dict[str, dict[str, Any]] = {}
    source_hashes: dict[str, str] = {}
    captured_at = require_aware_utc(
        created_at or as_of_utc,
        field="bundle created_at",
    )
    for name in REQUIRED_DATASETS:
        raw_payload = datasets[name]
        _reject_secret_keys(raw_payload, path=name)
        payload = canonical_payload(raw_payload)
        source_hash = _snapshot_hash(name, payload)
        source_hashes[name] = source_hash
        snapshots[source_hash] = {
            "id": source_hash,
            "content_hash": source_hash,
            "dataset": name,
            "effective_at": _iso_z(as_of_utc),
            "available_at": _iso_z(as_of_utc),
            "created_at": _iso_z(captured_at),
            "payload": payload,
            "record_version": 1,
        }

    identity = {
        "as_of": _iso_z(as_of_utc),
        "source_hashes": dict(sorted(source_hashes.items())),
        "code_revision": str(code_revision or "").strip(),
        "status": "finalized",
        "provenance": "strict_verified",
        "record_version": 1,
    }
    manifest_digest = hashlib.sha256(
        _canonical_json(identity).encode("utf-8")
    ).hexdigest()
    manifest = StoredManifest(
        manifest_id=f"pit-{manifest_digest}",
        as_of=as_of_utc,
        created_at=captured_at,
        source_hashes=source_hashes,
        code_revision=identity["code_revision"],
    )
    return manifest, snapshots


class _RegistrySnapshotStore:
    __slots__ = ("_registry", "_record")

    def __init__(self, registry, record: StoredManifest):
        self._registry = registry
        self._record = record

    def load_snapshot(self, *, dataset: str, context: PointInTimeContext):
        name = str(dataset or "").strip()
        expected_hash = self._record.source_hashes.get(name)
        if not expected_hash:
            raise PointInTimeDataError(
                f"{name} is not present in PIT manifest"
            )
        if context.manifest.manifest_id != self._record.manifest_id:
            raise PointInTimeDataError(
                "PIT snapshot store manifest does not match context"
            )
        row = self._registry._read_snapshot(expected_hash)
        if not isinstance(row, Mapping):
            raise PointInTimeDataError(
                f"{name} snapshot row is missing"
            )
        if str(row.get("dataset") or "") != name:
            raise PointInTimeDataError(
                f"{name} snapshot dataset identity mismatch"
            )
        payload = canonical_payload(row.get("payload"))
        actual_hash = _snapshot_hash(name, payload)
        if actual_hash != expected_hash or row.get("content_hash") != expected_hash:
            raise PointInTimeDataError(
                f"{name} snapshot hash mismatch"
            )
        if name == "graph":
            from point_in_time_graph import ReplayGraphDriver

            payload = ReplayGraphDriver(payload)
        return {
            "manifest_id": self._record.manifest_id,
            "effective_at": row.get("effective_at"),
            "available_at": row.get("available_at"),
            "payload": payload,
        }


class _RegistryBase:
    def resolve_bundle(self, as_of: datetime) -> ResolvedPointInTimeBundle:
        as_of_utc = require_aware_utc(as_of, field="bundle resolution as_of")
        record = self._resolve_manifest_record(as_of_utc)
        if record is None:
            raise PointInTimeDataError(
                f"no finalized point-in-time manifest exists at or before {_iso_z(as_of_utc)}"
            )
        manifest = DatasetManifest(
            manifest_id=record.manifest_id,
            source_hashes=record.source_hashes,
            created_at=record.created_at,
        )
        context = PointInTimeContext(
            as_of=as_of_utc,
            manifest=manifest,
            strict=True,
            is_live=False,
        )
        return ResolvedPointInTimeBundle(
            record=record,
            manifest=manifest,
            context=context,
            store=_RegistrySnapshotStore(self, record),
        )


class InMemoryPointInTimeRegistry(_RegistryBase):
    """Hermetic append-only implementation used by tests and local tooling."""

    def __init__(self):
        self._snapshots: dict[str, dict[str, Any]] = {}
        self._manifests: dict[str, StoredManifest] = {}

    def finalize_bundle(
        self,
        *,
        as_of: datetime,
        datasets: Mapping[str, Any],
        code_revision: str,
        created_at: datetime | None = None,
    ) -> StoredManifest:
        manifest, snapshots = _build_bundle_rows(
            as_of=as_of,
            datasets=datasets,
            code_revision=code_revision,
            created_at=created_at,
        )
        for source_hash, row in snapshots.items():
            existing = self._snapshots.get(source_hash)
            if existing is not None and not _snapshot_rows_match(existing, row):
                raise PointInTimeDataError(
                    "divergent immutable PIT snapshot row"
                )
            self._snapshots.setdefault(source_hash, row)
        existing_manifest = self._manifests.get(manifest.manifest_id)
        if existing_manifest is not None:
            if (
                existing_manifest.as_of != manifest.as_of
                or dict(existing_manifest.source_hashes)
                != dict(manifest.source_hashes)
                or existing_manifest.code_revision != manifest.code_revision
            ):
                raise PointInTimeDataError(
                    "divergent immutable PIT manifest row"
                )
            return existing_manifest
        self._manifests[manifest.manifest_id] = manifest
        return manifest

    def _resolve_manifest_record(self, as_of: datetime):
        eligible = [
            record
            for record in self._manifests.values()
            if record.status == "finalized" and record.as_of <= as_of
        ]
        return max(eligible, key=lambda record: record.as_of) if eligible else None

    def _read_snapshot(self, source_hash: str):
        return self._snapshots.get(source_hash)


class RethinkPointInTimeRegistry(_RegistryBase):
    """Append-only production registry stored in the shared RethinkDB."""

    def __init__(
        self,
        *,
        r_module=None,
        connect=None,
        db_name: str | None = None,
    ):
        if r_module is None:
            from rethinkdb import RethinkDB

            r_module = RethinkDB()
        self._r = r_module
        self._connect_override = connect
        self._db_name = str(
            db_name or os.environ.get("RETHINKDB_DB") or "IntelliStock"
        )

    @contextmanager
    def _connection(self):
        if self._connect_override is not None:
            conn = self._connect_override()
        else:
            conn = self._r.connect(
                host=os.environ.get("RETHINKDB_HOST", "localhost"),
                port=int(os.environ.get("RETHINKDB_PORT", "28015")),
                timeout=10,
            )
        try:
            yield conn
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def _ensure_tables(self, conn):
        databases = set(self._r.db_list().run(conn))
        if self._db_name not in databases:
            self._r.db_create(self._db_name).run(conn)
        tables = set(self._r.db(self._db_name).table_list().run(conn))
        for table in (SNAPSHOT_TABLE, MANIFEST_TABLE):
            if table not in tables:
                self._r.db(self._db_name).table_create(table).run(conn)

    def _insert_immutable(self, conn, table: str, row: Mapping[str, Any]):
        existing = (
            self._r.db(self._db_name).table(table).get(row["id"]).run(conn)
        )
        if existing is not None:
            rows_match = (
                _snapshot_rows_match(existing, row)
                if table == SNAPSHOT_TABLE
                else canonical_payload(existing) == canonical_payload(row)
            )
            if not rows_match:
                raise PointInTimeDataError(
                    f"divergent immutable {table} row"
                )
            return
        self._r.db(self._db_name).table(table).insert(
            dict(row), conflict="error"
        ).run(conn)

    def finalize_bundle(
        self,
        *,
        as_of: datetime,
        datasets: Mapping[str, Any],
        code_revision: str,
        created_at: datetime | None = None,
    ) -> StoredManifest:
        manifest, snapshots = _build_bundle_rows(
            as_of=as_of,
            datasets=datasets,
            code_revision=code_revision,
            created_at=created_at,
        )
        with self._connection() as conn:
            self._ensure_tables(conn)
            for row in snapshots.values():
                self._insert_immutable(conn, SNAPSHOT_TABLE, row)
            # Manifest publication is intentionally last: readers can never
            # resolve a manifest whose snapshots have not been verified.
            self._insert_immutable(conn, MANIFEST_TABLE, manifest.to_doc())
        return manifest

    def _resolve_manifest_record(self, as_of: datetime):
        with self._connection() as conn:
            self._ensure_tables(conn)
            rows = list(
                self._r.db(self._db_name)
                .table(MANIFEST_TABLE)
                .filter(
                    lambda row: (row["status"] == "finalized")
                    & (row["as_of"] <= _iso_z(as_of))
                )
                .order_by(self._r.desc("as_of"))
                .limit(1)
                .run(conn)
            )
        return StoredManifest.from_doc(rows[0]) if rows else None

    def _read_snapshot(self, source_hash: str):
        with self._connection() as conn:
            self._ensure_tables(conn)
            return (
                self._r.db(self._db_name)
                .table(SNAPSHOT_TABLE)
                .get(source_hash)
                .run(conn)
            )


def resolve_default_bundle(as_of: datetime) -> ResolvedPointInTimeBundle:
    """Resolve from the shared production registry without serialized callables."""

    return RethinkPointInTimeRegistry().resolve_bundle(as_of)


__all__ = [
    "InMemoryPointInTimeRegistry",
    "MANIFEST_TABLE",
    "REQUIRED_DATASETS",
    "ResolvedPointInTimeBundle",
    "RethinkPointInTimeRegistry",
    "SNAPSHOT_TABLE",
    "StoredManifest",
    "canonical_payload",
    "content_hash",
    "resolve_default_bundle",
]
