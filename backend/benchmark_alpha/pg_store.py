"""Authoritative Postgres event and state store for benchmark alpha (Task 5).

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

from db import store as _store
from db import schema as _schema
from db.errors import StoreError
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

# The compound ReQL indexes ``read_by_index`` addressed, as their ordered
# field lists. The last component is always ``as_of``.
_INDEX_FIELDS = {
    "run_asof": ("run_id", "as_of"),
    "instance_origin_asof": ("instance_id", "origin", "as_of"),
}


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


class PostgresBackend:
    """Production backend over ``db.store``.

    ``r_module``/``conn_factory``/``db_name`` are accepted and ignored: the
    store takes its own pooled connection per operation. They stay in the
    signature because broker.py, api/main.py, watchdog_main.py and
    backend/scripts construct this positionally.
    """

    def __init__(self, r_module=None, conn_factory=None, db_name=DB_NAME):
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
        AlphaUnavailableError instead of the idempotent/divergent verdict).

        ``durability`` is accepted and ignored -- Postgres is durable by
        default -- and stays in the signature because the ~10 test doubles
        implementing this interface depend on the keyword.
        """
        existing = _store.get(table, doc["id"])
        if existing is not None:
            return existing
        res = _store.insert(table, doc, conflict="error", durability=durability)
        if not res["errors"]:
            return None
        if self._is_duplicate_key_error(res["first_error"] or ""):
            return _store.get(table, doc["id"])
        raise StoreError(res["first_error"] or "insert failed")

    def insert_event(self, doc, *, durability):
        return self._insert_once(EVENTS_TABLE, doc, durability)

    def insert_record(self, table, doc, *, durability):
        return self._insert_once(table, doc, durability)

    def compare_and_swap_state(self, key, expected_version, doc, *, durability):
        """Atomic server-side compare-and-swap (audit 2026-07-18: the prior
        get-then-insert allowed two same-version writers to both succeed).

        The ReQL form nested two branches so that a MISSING row replaced
        itself with None (a no-op reported as skipped, i.e. ``None`` here) and
        was never conflated with a failed predicate. ``store.replace_if``
        makes that distinction explicit: it RAISES on a missing row, which is
        translated back to ``None`` below.
        """
        expected = int(expected_version)
        if expected == 0:
            res = _store.insert(STATE_TABLE, doc, conflict="error",
                                durability=durability)
            if not res["errors"]:
                return doc
            if self._is_duplicate_key_error(res["first_error"] or ""):
                return None  # row exists: version 0 expectation stale
            raise StoreError(res["first_error"] or "insert failed")
        # The dict predicate carries the guarded ::numeric compare, so a
        # version stored as 3.0 still matches an expected 3, as it did in ReQL.
        when = _store.predicate({"version": expected})
        try:
            saved = _store.replace_if(STATE_TABLE, str(key), when=when, doc=doc)
        except StoreError:
            if _store.get(STATE_TABLE, str(key)) is None:
                return None          # row missing: r.branch(row.eq(None), row, ...)
            raise
        return doc if saved is not None else None

    def get_state_row(self, key):
        return _store.get(STATE_TABLE, str(key))

    def get_record(self, table, record_id):
        return _store.get(table, str(record_id))

    def list_records(self, table, filters=None, order_field=None):
        query = _store.Selection(table)
        if filters:
            query = _store.filter(
                table, {str(field): value for field, value in filters.items()})
        if order_field:
            query = _store.order_by(
                query, fields=(_store.asc(str(order_field)),))
        return _store.run(query)

    def count_records(self, table, filters=None):
        query = _store.Selection(table)
        if filters:
            query = _store.filter(
                table, {str(field): value for field, value in filters.items()})
        return int(_store.count(query))

    def read_by_index(self, table, index, key, *, limit, cursor):
        """One bounded page over a compound index whose last component is
        ``as_of``. The cursor is an opaque boundary position (as_of + ids
        already emitted at that as_of) so tied rows are never dropped.

        The compound index is reproduced as equality on its leading
        components plus a CLOSED lower bound on ``as_of``: with the leading
        components pinned, ordering by ``as_of`` is the index order. ``id`` is
        the explicit tiebreak, which is what RethinkDB's secondary index used
        implicitly.
        """
        state = json.loads(cursor) if cursor else {"as_of": None, "seen": []}
        boundary = state.get("as_of")
        overfetch = int(limit) + len(state.get("seen") or []) + 1
        fields = _INDEX_FIELDS.get(str(index))
        if fields is None:
            raise StoreError("unknown alpha index %r" % (index,))
        leading = fields[:-1]
        if len(key) != len(leading):
            raise StoreError(
                "index %r takes %d key components, got %d"
                % (index, len(leading), len(key)))
        pred = None
        for name, value in zip(leading, key):
            term = _store.P.field(name).eq(value)
            pred = term if pred is None else (pred & term)
        if boundary is not None:
            term = _store.P.field(fields[-1]).ge(boundary)
            pred = term if pred is None else (pred & term)
        sel = (_store.filter(table, pred) if pred is not None
               else _store.Selection(table))
        sel = _store.order_by(
            sel, fields=(_store.asc(fields[-1]), _store.asc("id")))
        rows = _store.run(_store.limit(sel, overfetch))
        return advance_page(rows, limit, cursor)

    def health_probe(self):
        try:
            tables = set(_store.table_list())
            missing = {EVENTS_TABLE, STATE_TABLE, EXPERIMENTS_TABLE} - tables
            if missing:
                return f"missing tables: {sorted(missing)}"
            return None
        except Exception as exc:
            return f"{type(exc).__name__}: {exc}"


# The class was named for the driver, not for what it does. Both names refer
# to the same object so broker.py / api/main.py (other port groups) keep
# importing through the compat module benchmark_alpha/rethink_store.py.
_RethinkBackend = PostgresBackend


class AlphaPostgresStore:
    """Sole persistence boundary for alpha events and versioned state."""

    def __init__(self, r_module=None, conn_factory=None, db_name=DB_NAME):
        self._backend = PostgresBackend(r_module, conn_factory, db_name)

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


# Historical name. Kept so the call sites in other port groups (broker.py,
# api/main.py, backend/scripts) import the same object.
AlphaRethinkStore = AlphaPostgresStore


def ensure_alpha_tables():
    """Idempotent DDL for every table this store writes."""
    _schema.ensure_schema(tables=(EVENTS_TABLE, STATE_TABLE, EXPERIMENTS_TABLE))
