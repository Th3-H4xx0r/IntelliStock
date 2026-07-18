"""Authoritative RethinkDB event and state store for benchmark alpha (Task 5).

Contracts:
- Events are append-only, written with HARD durability. An already-existing
  byte-identical event ID is idempotent (returns False); divergent content
  under the same ID raises ``AlphaIntegrityError``.
- Mutable state uses compare-and-swap versions; a stale expected version
  raises ``AlphaStateConflictError`` instead of silently overwriting.
- A storage failure raises ``AlphaUnavailableError`` — it is NEVER returned
  as an empty successful read. Callers block exposure increases on it.
- Run state (``run:<instance_id>:<run_id>``) persists the typed ``RunPhase``
  so a restart resumes the first incomplete phase rather than trusting a
  date-only completed-cycle marker.
"""
import json
from dataclasses import dataclass
from datetime import datetime, timezone

from benchmark_alpha.types import EventKind, RunPhase

DB_NAME = "IntelliStock"
EVENTS_TABLE = "AlphaEvents"
STATE_TABLE = "AlphaState"

_CONNECT_TIMEOUT_SECONDS = 10


class AlphaIntegrityError(Exception):
    """Same event ID with divergent content — an audit-integrity violation."""


class AlphaStateConflictError(Exception):
    """Compare-and-swap failed: the expected state version is stale."""


class AlphaUnavailableError(Exception):
    """The store is unreachable/unhealthy. Never masquerades as empty data."""


def canonical_json(value):
    """Deterministic byte representation for content-identity comparison."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _require_aware(name, value):
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError(f"{name} must be a timezone-aware datetime")


@dataclass(frozen=True)
class StateRecord:
    key: str
    version: int
    payload: dict
    updated_at: str = ""


@dataclass(frozen=True)
class RunStateRecord:
    instance_id: str
    run_id: str
    phase: RunPhase
    payload: dict
    version: int


@dataclass(frozen=True)
class AlphaStoreHealth:
    available: bool
    error: str = ""


def run_state_key(instance_id, run_id):
    return f"run:{instance_id}:{run_id}"


class _RethinkBackend:
    """Production backend over the shared bounded-connection pattern.

    Connection parameters resolve exactly like ``nexus_runtime_state`` (env
    RETHINKDB_HOST/PORT); credentials are never logged.
    """

    def __init__(self, r_module, conn_factory, db_name=DB_NAME):
        self._r = r_module
        self._conn_factory = conn_factory
        self._db = db_name

    def insert_event(self, doc, *, durability):
        with self._conn_factory() as conn:
            existing = self._r.db(self._db).table(EVENTS_TABLE).get(doc["id"]).run(conn)
            if existing is not None:
                return existing
            self._r.db(self._db).table(EVENTS_TABLE).insert(
                doc, conflict="error", durability=durability).run(conn)
            return None

    def compare_and_swap_state(self, key, expected_version, doc, *, durability):
        with self._conn_factory() as conn:
            table = self._r.db(self._db).table(STATE_TABLE)
            prior = table.get(key).run(conn)
            version = 0 if prior is None else int(prior.get("version") or 0)
            if version != expected_version:
                return None
            table.insert(doc, conflict="replace", durability=durability).run(conn)
            return doc

    def get_state_row(self, key):
        with self._conn_factory() as conn:
            return self._r.db(self._db).table(STATE_TABLE).get(key).run(conn)

    def health_probe(self):
        try:
            with self._conn_factory() as conn:
                tables = set(self._r.db(self._db).table_list().run(conn))
            missing = {EVENTS_TABLE, STATE_TABLE} - tables
            if missing:
                return f"missing tables: {sorted(missing)}"
            return None
        except Exception as exc:
            return f"{type(exc).__name__}: {exc}"


class AlphaRethinkStore:
    """Sole persistence boundary for alpha events and versioned state."""

    def __init__(self, r_module, conn_factory, db_name=DB_NAME):
        self._backend = _RethinkBackend(r_module, conn_factory, db_name)

    @classmethod
    def for_backend(cls, backend):
        """Inject a deterministic backend (tests) without changing semantics."""
        store = cls.__new__(cls)
        store._backend = backend
        return store

    # -- events ---------------------------------------------------------------

    def append_event(self, event_id, kind, payload, created_at):
        if not isinstance(kind, EventKind):
            raise ValueError(f"kind must be an EventKind, got {kind!r}")
        _require_aware("created_at", created_at)
        doc = {
            "id": str(event_id),
            "kind": kind.value,
            "payload": payload,
            "created_at": created_at.isoformat(),
        }
        try:
            prior = self._backend.insert_event(doc, durability="hard")
        except (AlphaIntegrityError, AlphaUnavailableError):
            raise
        except Exception as exc:
            raise AlphaUnavailableError(
                f"append_event failed: {type(exc).__name__}: {exc}") from exc
        if prior is None:
            return True
        prior_identity = canonical_json({
            "kind": prior.get("kind"),
            "payload": prior.get("payload"),
            "created_at": prior.get("created_at"),
        })
        new_identity = canonical_json({
            "kind": doc["kind"], "payload": doc["payload"],
            "created_at": doc["created_at"],
        })
        if prior_identity == new_identity:
            return False
        raise AlphaIntegrityError(
            f"event {event_id!r} already exists with divergent content")

    # -- versioned state ------------------------------------------------------

    def put_state(self, key, payload, expected_version):
        doc = {
            "id": str(key),
            "version": int(expected_version) + 1,
            "payload": payload,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        try:
            written = self._backend.compare_and_swap_state(
                str(key), int(expected_version), doc, durability="hard")
        except Exception as exc:
            raise AlphaUnavailableError(
                f"put_state failed: {type(exc).__name__}: {exc}") from exc
        if written is None:
            raise AlphaStateConflictError(
                f"stale expected_version={expected_version} for state {key!r}")
        return StateRecord(key=str(key), version=doc["version"],
                           payload=payload, updated_at=doc["updated_at"])

    def get_state(self, key):
        try:
            row = self._backend.get_state_row(str(key))
        except Exception as exc:
            raise AlphaUnavailableError(
                f"get_state failed: {type(exc).__name__}: {exc}") from exc
        if row is None:
            return None
        return StateRecord(
            key=str(key), version=int(row.get("version") or 0),
            payload=row.get("payload"), updated_at=str(row.get("updated_at") or ""))

    # -- run phase state ------------------------------------------------------

    def put_run_state(self, instance_id, run_id, phase, payload, expected_version):
        if not isinstance(phase, RunPhase):
            raise ValueError(f"phase must be a RunPhase, got {phase!r}")
        return self.put_state(
            run_state_key(instance_id, run_id),
            {"phase": phase.value, **dict(payload or {})},
            expected_version,
        )

    def get_run_state(self, instance_id, run_id):
        record = self.get_state(run_state_key(instance_id, run_id))
        if record is None:
            return None
        payload = dict(record.payload or {})
        phase = RunPhase(payload.pop("phase"))
        return RunStateRecord(
            instance_id=str(instance_id), run_id=str(run_id),
            phase=phase, payload=payload, version=record.version)

    # -- health ---------------------------------------------------------------

    def health(self):
        try:
            error = self._backend.health_probe()
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
        if error:
            return AlphaStoreHealth(available=False, error=str(error))
        return AlphaStoreHealth(available=True)
