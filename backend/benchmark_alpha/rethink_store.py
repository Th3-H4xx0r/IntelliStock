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
EXPERIMENTS_TABLE = "AlphaExperiments"
# Registrations and terminal outcomes are separate append-only rows in the
# existing migrated table. Keep the alias for callers that name the logical
# outcome stream; no second, undeclared production table is required.
EXPERIMENT_OUTCOMES_TABLE = EXPERIMENTS_TABLE

_CONNECT_TIMEOUT_SECONDS = 10


class AlphaIntegrityError(Exception):
    """Same event ID with divergent content — an audit-integrity violation."""


class AlphaStateConflictError(Exception):
    """Compare-and-swap failed: the expected state version is stale."""


class AlphaUnavailableError(Exception):
    """The store is unreachable/unhealthy. Never masquerades as empty data."""


def _json_normalize(value):
    """Normalize to what a JSON store round-trips: string keys, and all
    non-bool numbers as floats (RethinkDB stores doubles; the driver returns
    integral doubles as ints — audit 2026-07-18: a byte-identical retry of
    equity=100.0 read back as 100 raised a false integrity error)."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, dict):
        return {str(k): _json_normalize(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_normalize(v) for v in value]
    return value


def canonical_json(value):
    """Deterministic byte representation for content-identity comparison."""
    return json.dumps(_json_normalize(value), sort_keys=True,
                      separators=(",", ":"), default=str)


def advance_page(rows_from_boundary, limit, cursor):
    """Boundary-safe pagination over rows ordered by ``as_of``.

    ``rows_from_boundary`` are rows at-or-after the cursor's boundary
    (CLOSED bound). The cursor carries the boundary ``as_of`` plus the ids
    already emitted AT that boundary, so rows sharing the boundary
    timestamp are never dropped (audit 2026-07-18: an open bound on an
    as_of-only cursor skipped every tied row)."""
    limit = int(limit)
    state = json.loads(cursor) if cursor else {"as_of": None, "seen": []}
    seen = set(state.get("seen") or [])
    boundary = state.get("as_of")
    page = []
    for row in rows_from_boundary:
        if boundary is not None and row.get("as_of") == boundary \
                and row.get("id") in seen:
            continue
        page.append(row)
        if len(page) >= limit:
            break
    if len(page) < limit:
        return page, None
    last_as_of = page[-1].get("as_of")
    seen_at_boundary = [r.get("id") for r in page if r.get("as_of") == last_as_of]
    if boundary == last_as_of:
        seen_at_boundary = list(seen) + seen_at_boundary
    return page, json.dumps({"as_of": last_as_of, "seen": seen_at_boundary})


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

    @staticmethod
    def _is_duplicate_key_error(exc):
        return "Duplicate primary key" in str(exc)

    def _insert_once(self, table, doc, durability):
        """Read-then-insert with the concurrent-duplicate race resolved: a
        losing racer re-reads the winner's row and returns it as ``prior``
        (audit 2026-07-18: the race previously surfaced as
        AlphaUnavailableError instead of the idempotent/divergent verdict)."""
        with self._conn_factory() as conn:
            t = self._r.db(self._db).table(table)
            existing = t.get(doc["id"]).run(conn)
            if existing is not None:
                return existing
            try:
                t.insert(doc, conflict="error", durability=durability).run(conn)
                return None
            except Exception as exc:
                if self._is_duplicate_key_error(exc):
                    return t.get(doc["id"]).run(conn)
                raise

    def insert_event(self, doc, *, durability):
        return self._insert_once(EVENTS_TABLE, doc, durability)

    def insert_record(self, table, doc, *, durability):
        return self._insert_once(table, doc, durability)

    def compare_and_swap_state(self, key, expected_version, doc, *, durability):
        """Atomic server-side compare-and-swap (audit 2026-07-18: the prior
        get-then-insert allowed two same-version writers to both succeed)."""
        r = self._r
        expected = int(expected_version)
        with self._conn_factory() as conn:
            table = r.db(self._db).table(STATE_TABLE)
            if expected == 0:
                try:
                    table.insert(doc, conflict="error",
                                 durability=durability).run(conn)
                    return doc
                except Exception as exc:
                    if self._is_duplicate_key_error(exc):
                        return None  # row exists: version 0 expectation stale
                    raise
            result = table.get(str(key)).replace(
                lambda row: r.branch(
                    row.eq(None), row,
                    r.branch(row["version"].eq(expected), r.expr(doc), row)),
                durability=durability).run(conn)
            return doc if int(result.get("replaced", 0) or 0) == 1 else None

    def get_state_row(self, key):
        with self._conn_factory() as conn:
            return self._r.db(self._db).table(STATE_TABLE).get(key).run(conn)

    def get_record(self, table, record_id):
        with self._conn_factory() as conn:
            return (
                self._r.db(self._db).table(table).get(str(record_id)).run(conn)
            )

    def list_records(self, table, filters=None, order_field=None):
        with self._conn_factory() as conn:
            query = self._r.db(self._db).table(table)
            if filters:
                query = query.filter(
                    {str(field): value for field, value in filters.items()}
                )
            if order_field:
                query = query.order_by(str(order_field))
            return list(query.run(conn))

    def count_records(self, table, filters=None):
        with self._conn_factory() as conn:
            query = self._r.db(self._db).table(table)
            if filters:
                query = query.filter(
                    {str(field): value for field, value in filters.items()}
                )
            return int(query.count().run(conn))

    def read_by_index(self, table, index, key, *, limit, cursor):
        """One bounded page over a compound index whose last component is
        ``as_of``. The cursor is an opaque boundary position (as_of + ids
        already emitted at that as_of) so tied rows are never dropped."""
        state = json.loads(cursor) if cursor else {"as_of": None, "seen": []}
        boundary = state.get("as_of")
        overfetch = int(limit) + len(state.get("seen") or []) + 1
        with self._conn_factory() as conn:
            t = self._r.db(self._db).table(table)
            lo = list(key) + ([boundary] if boundary is not None else [self._r.minval])
            hi = list(key) + [self._r.maxval]
            rows = list(
                t.between(lo, hi, index=index, left_bound="closed")
                .order_by(index=index).limit(overfetch).run(conn))
        return advance_page(rows, limit, cursor)

    def health_probe(self):
        try:
            with self._conn_factory() as conn:
                tables = set(self._r.db(self._db).table_list().run(conn))
            missing = {EVENTS_TABLE, STATE_TABLE, EXPERIMENTS_TABLE} - tables
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

    # -- typed records (Task 7) ----------------------------------------------

    def write_record(self, record):
        """Write a typed record to its table with hard durability.

        Byte-identical existing content is idempotent (returns False);
        divergent content under the same natural-identity ID raises
        ``AlphaIntegrityError``; storage failure raises
        ``AlphaUnavailableError`` — never an empty success."""
        doc = record.to_doc()
        table = record.TABLE
        try:
            prior = self._backend.insert_record(table, doc, durability="hard")
        except (AlphaIntegrityError, AlphaUnavailableError):
            raise
        except Exception as exc:
            raise AlphaUnavailableError(
                f"write_record({table}) failed: {type(exc).__name__}: {exc}"
            ) from exc
        if prior is None:
            return True
        if canonical_json(prior) == canonical_json(doc):
            return False
        raise AlphaIntegrityError(
            f"record {doc.get('id')!r} in {table} already exists with "
            "divergent content")

    # -- immutable experiment attempts ---------------------------------------

    def _insert_immutable_doc(self, table, doc, operation):
        try:
            prior = self._backend.insert_record(
                table, doc, durability="hard"
            )
        except (AlphaIntegrityError, AlphaUnavailableError):
            raise
        except Exception as exc:
            raise AlphaUnavailableError(
                f"{operation} failed: {type(exc).__name__}: {exc}"
            ) from exc
        if prior is None:
            return True
        if canonical_json(prior) == canonical_json(doc):
            return False
        raise AlphaIntegrityError(
            f"immutable record {doc.get('id')!r} in {table} already exists "
            "with divergent content"
        )

    def insert_experiment_registration(self, registration):
        from experiment_registry import RegisteredExperiment

        if not isinstance(registration, RegisteredExperiment):
            raise TypeError("registration must be a RegisteredExperiment")
        return self._insert_immutable_doc(
            EXPERIMENTS_TABLE,
            registration.to_doc(),
            "insert_experiment_registration",
        )

    def insert_experiment_outcome(self, outcome):
        from experiment_registry import ExperimentOutcome

        if not isinstance(outcome, ExperimentOutcome):
            raise TypeError("outcome must be an ExperimentOutcome")
        return self._insert_immutable_doc(
            EXPERIMENT_OUTCOMES_TABLE,
            outcome.to_doc(),
            "insert_experiment_outcome",
        )

    def get_experiment_registration(self, experiment_id):
        from experiment_registry import RegisteredExperiment

        try:
            row = self._backend.get_record(
                EXPERIMENTS_TABLE, str(experiment_id)
            )
        except Exception as exc:
            raise AlphaUnavailableError(
                "get_experiment_registration failed: "
                f"{type(exc).__name__}: {exc}"
            ) from exc
        return RegisteredExperiment.from_doc(row) if row is not None else None

    def get_experiment_outcome(self, experiment_id):
        from experiment_registry import ExperimentOutcome

        try:
            row = self._backend.get_record(
                EXPERIMENT_OUTCOMES_TABLE, f"{experiment_id}:terminal"
            )
        except Exception as exc:
            raise AlphaUnavailableError(
                f"get_experiment_outcome failed: {type(exc).__name__}: {exc}"
            ) from exc
        return ExperimentOutcome.from_doc(row) if row is not None else None

    def list_experiment_registrations(self, scope=None):
        from experiment_registry import RegisteredExperiment

        filters = {"record_kind": "registration"}
        if scope is not None:
            filters["search_scope"] = str(scope)
        try:
            rows = self._backend.list_records(
                EXPERIMENTS_TABLE,
                filters,
                "registered_at",
            )
        except Exception as exc:
            raise AlphaUnavailableError(
                "list_experiment_registrations failed: "
                f"{type(exc).__name__}: {exc}"
            ) from exc
        return tuple(RegisteredExperiment.from_doc(row) for row in rows)

    def experiment_trial_count(self, scope=None):
        filters = {"record_kind": "registration"}
        if scope is not None:
            filters["search_scope"] = str(scope)
        try:
            return self._backend.count_records(
                EXPERIMENTS_TABLE,
                filters,
            )
        except Exception as exc:
            raise AlphaUnavailableError(
                f"experiment_trial_count failed: {type(exc).__name__}: {exc}"
            ) from exc

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
