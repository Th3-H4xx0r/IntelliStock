"""One-shot RethinkDB -> PostgreSQL migration: export, convert, COPY, verify.

The ONLY file in the tree allowed to import ``rethinkdb``, and it imports it
lazily inside :func:`rethink_conn` so nothing else pays for the dependency.

Shape of a run::

    scripts/migrate_rethinkdb_to_postgres.py                    # copy everything
    scripts/migrate_rethinkdb_to_postgres.py --tables Instances # one table
    scripts/migrate_rethinkdb_to_postgres.py --verify --verify-sample 1.0

Three things the design turns on:

* **Paging is by primary key**, never ``skip()`` -- ``skip`` is O(n^2) on
  RethinkDB and PriceHistory is 2.86M rows.
* **Every batch lands through a TEMP table and ``ON CONFLICT ... DO UPDATE``**,
  so re-running a partial batch is a no-op. A ``_migration_state`` row per
  table records the last primary key written, so a killed run continues rather
  than restarting or duplicating.
* **Documents are compared, not summarised.** ``--verify`` hashes whole
  documents with :func:`db.json.canonical_sha256` and writes both sides of any
  mismatch to disk.

Two corrections to the design brief, both from the running system:

* ``time_format`` is a **run option**, not a connect option: this driver's
  ``r.connect(time_format="raw")`` raises ``TypeError``. It is passed to every
  ``.run()`` instead (:data:`_RUN_OPTS`).
* The BacktestResults split lives in ``backend/backtest_result_store.py`` and
  numbers step rows from **seq 1**, with a ``seq=0`` marker row carrying a JSON
  ``null`` doc so "finalised with zero entries" stays distinguishable from
  "never finalised". This script reproduces that convention exactly rather
  than inventing a 0-based one; ``test_migration_script.py`` pins the two
  writers against each other row for row.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json as _stdjson
import os
import pathlib
import random
import sys
from typing import Any, Iterator

_BACKEND = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

import backtest_result_store as brs                      # noqa: E402
from db import pool, schema, store                       # noqa: E402
from db.json import canonical_sha256, dumps              # noqa: E402

MIGRATION_STATE_TABLE = "_migration_state"
DEFAULT_BATCH = 2000
BACKTEST_BATCH = 200
STEP_CHUNK = 20000        # BacktestSteps rows per COPY
MISMATCH_DIR = ".migration-mismatches"

#: RethinkDB pseudotypes must arrive RAW. ``native`` hands back ``datetime``
#: objects ``json.dumps`` cannot serialise, and native binary hands back
#: ``bytes``; both are unrepresentable in a jsonb document.
_RUN_OPTS = {"time_format": "raw", "binary_format": "raw"}

#: ReQL secondary indexes that the BacktestResults split MOVED to another
#: table. ``status_norm`` indexes ``status``, which now lives on the hot
#: BacktestProgress row, so looking for it on BacktestResults would report a
#: missing index that is in fact present -- one table to the left.
_MOVED_INDEXES = {"BacktestResults": {"status_norm": "BacktestProgress"}}


# --------------------------------------------------------------------------
# export
# --------------------------------------------------------------------------

def rethink_db() -> str:
    return os.environ.get("RETHINKDB_DB", "IntelliStock")


def rethink_conn():
    """Lazy import -- this is the one place ``rethinkdb`` may be named."""
    from rethinkdb import RethinkDB
    r = RethinkDB()
    conn = r.connect(
        host=os.environ.get("RETHINKDB_HOST", "localhost"),
        port=int(os.environ.get("RETHINKDB_PORT", "28015")),
        db=rethink_db(),
        timeout=int(os.environ.get("RETHINKDB_TIMEOUT", "30")),
    )
    return r, conn


def iso(value: Any) -> Any:
    """RethinkDB TIME pseudotype (raw form) -> ISO-8601 with offset. Recursive.

    Every other pseudotype (BINARY's base64 envelope, GEOMETRY) is already a
    plain JSON object and is left VERBATIM: rewriting it would change the
    document, and the invariant is that documents survive unchanged.
    """
    if isinstance(value, dict):
        if value.get("$reql_type$") == "TIME":
            tz = value.get("timezone") or "+00:00"
            sign = -1 if tz[0] == "-" else 1
            hours, minutes = int(tz[1:3]), int(tz[4:6])
            offset = _dt.timezone(sign * _dt.timedelta(hours=hours, minutes=minutes))
            return _dt.datetime.fromtimestamp(
                value["epoch_time"], tz=_dt.timezone.utc).astimezone(offset).isoformat()
        return {k: iso(v) for k, v in value.items()}
    if isinstance(value, list):
        return [iso(v) for v in value]
    return value


def export_table(table: str, *, since_id: Any = None,
                 batch: int = DEFAULT_BATCH) -> Iterator[list]:
    """Page by primary key. NEVER skip() -- it is O(n^2) on RethinkDB.

    The primary index is named after the primary key, so the same query works
    for ``sports_fixtures`` (``fixture_id``) as for the 116 ``id`` tables.
    """
    r, conn = rethink_conn()
    pk = schema.spec(table).pk_field
    last = since_id
    try:
        while True:
            q = r.db(rethink_db()).table(table)
            if last is not None:
                q = q.between(last, r.maxval, index=pk, left_bound="open")
            rows = [iso(row) for row in
                    q.order_by(index=pk).limit(batch).run(conn, **_RUN_OPTS)]
            if not rows:
                return
            yield rows
            if len(rows) < batch:
                return
            last = rows[-1][pk]
    finally:
        conn.close()


def rethink_table_list() -> list:
    r, conn = rethink_conn()
    try:
        return sorted(r.db(rethink_db()).table_list().run(conn, **_RUN_OPTS))
    finally:
        conn.close()


def rethink_count(table: str) -> int:
    r, conn = rethink_conn()
    try:
        return int(r.db(rethink_db()).table(table).count().run(conn, **_RUN_OPTS))
    finally:
        conn.close()


# --------------------------------------------------------------------------
# document -> row
# --------------------------------------------------------------------------

def pg_id(table: str, doc: dict) -> str:
    """The RethinkDB primary key as the Postgres ``id`` text.

    Deliberately NOT ``store.coerce_id``: coerce_id enforces the registry's
    declared ``id_type``, and enforcing it *here* would drop rows the registry
    is simply wrong about. The copy stays faithful and
    :func:`verify_id_types` reports the disagreement instead -- see the
    Instances finding in the report.
    """
    pk = schema.spec(table).pk_field
    value = doc.get(pk)
    if value is None:
        raise ValueError("%s: row is missing its primary key %r" % (table, pk))
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        raise ValueError("%s: primary key %r is a boolean" % (table, pk))
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, (int, float)):
        return str(value)
    raise ValueError("%s: primary key %r is a %s, which has no text form"
                     % (table, pk, type(value).__name__))


def split_backtest_row(doc: dict) -> tuple:
    """``(metadata_doc, [(kind, seq, step_doc)], progress_row)``.

    Every key that is NOT one of the six step arrays stays in ``doc``,
    verbatim and unsplit. That is what makes byte-identical assembly provable:
    nothing has to be re-derived.

    The step list is exactly what ``backtest_result_store.finalize_steps``
    writes for a terminal document: a ``seq=0`` marker with a JSON ``null``
    doc per present kind, then the array entries at ``seq`` 1..N. ``final`` is
    true for every one of them -- a migrated row is by definition finished
    being written.

    ``progress_row`` is None when the source document carries none of the four
    hot keys, matching ``write_split``, which skips the progress write there.
    """
    meta, steps_by_kind, payload = brs.split_doc(doc)
    steps: list = []
    for kind in brs.STEP_KINDS:
        entries = steps_by_kind.get(kind)
        if entries is None:
            continue                          # absent stays absent
        steps.append((kind, 0, None))         # the "finalised" marker row
        for seq, entry in enumerate(entries, start=1):
            steps.append((kind, seq, entry))
    if not payload:
        return meta, steps, None
    return meta, steps, {"id": pg_id(brs.RESULTS_TABLE, doc),
                         "payload": payload,
                         "last_active": _last_active(payload)}


def _last_active(payload: dict):
    """``write_progress``'s rule, verbatim: parse ``_last_active`` or NULL."""
    raw = payload.get("_last_active")
    if not raw:
        return None
    try:
        return _dt.datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None


def promote_price_history(doc: dict) -> dict:
    """PriceHistory gets ticker/ts as real columns for the partitioned PK."""
    ticker = doc.get("ticker")
    if not isinstance(ticker, str) or not ticker:
        raise ValueError("PriceHistory row %r has no ticker" % doc.get("id"))
    ts = doc.get("timestamp")
    if not ts:
        raise ValueError("PriceHistory row %r has no timestamp" % doc.get("id"))
    return {"ticker": ticker, "ts": _parse_ts("PriceHistory", doc.get("id"), ts),
            "id": pg_id("PriceHistory", doc), "doc": doc}


def _parse_ts(table: str, row_id, value) -> _dt.datetime:
    """A row whose timestamp will not parse is REJECTED, never silently dropped."""
    if isinstance(value, _dt.datetime):
        return value if value.tzinfo else value.replace(tzinfo=_dt.timezone.utc)
    if isinstance(value, str):
        text = value[:-1] + "+00:00" if value.endswith("Z") else value
        try:
            parsed = _dt.datetime.fromisoformat(text)
        except ValueError:
            parsed = None
        if parsed is not None:
            return parsed if parsed.tzinfo else parsed.replace(
                tzinfo=_dt.timezone.utc)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return _dt.datetime.fromtimestamp(float(value), _dt.timezone.utc)
    raise ValueError("%s row %r: timestamp %r will not parse"
                     % (table, row_id, value))


# --------------------------------------------------------------------------
# COPY
# --------------------------------------------------------------------------

_DEFAULT_SHAPE = (("id", "text"), ("doc", "jsonb"))
_SHAPES = {
    "PriceHistory": (("ticker", "text"), ("ts", "timestamptz"),
                     ("id", "text"), ("doc", "jsonb")),
    "BacktestSteps": (("backtest_id", "text"), ("kind", "text"),
                      ("seq", "bigint"), ("final", "boolean"), ("doc", "jsonb")),
    "BacktestProgress": (("id", "text"), ("payload", "jsonb"),
                         ("last_active", "timestamptz")),
}
#: The real conflict target, which is NOT always ``(id)``: PriceHistory's PK is
#: ``(ticker, ts, id)`` and BacktestSteps' is ``(backtest_id, kind, final, seq)``.
#: ``schema.spec(...).pk`` knows the first; the second is declared in raw DDL.
_CONFLICT_TARGETS = {"BacktestSteps": ("backtest_id", "kind", "final", "seq")}
#: Every table but BacktestSteps carries ``updated_at``.
_NO_UPDATED_AT = frozenset({"BacktestSteps"})


def _shape(table: str) -> tuple:
    return _SHAPES.get(table, _DEFAULT_SHAPE)


def _conflict_target(table: str) -> tuple:
    return _CONFLICT_TARGETS.get(table) or tuple(schema.spec(table).pk)


def _encode_rows(table: str, rows: list) -> list:
    """Documents -> COPY tuples. Encoding happens for the WHOLE batch before
    anything is written, so a NaN in row 400 cannot leave rows 1-399 copied."""
    if table == "PriceHistory":
        prepared = [promote_price_history(doc) for doc in rows]
    elif table in ("BacktestSteps", "BacktestProgress"):
        prepared = list(rows)
    else:
        prepared = [{"id": pg_id(table, doc), "doc": doc} for doc in rows]
    out = []
    for row in prepared:
        values = []
        for name, sql_type in _shape(table):
            value = row.get(name)
            values.append(dumps(value) if sql_type == "jsonb" else value)
        out.append(tuple(values))
    return out


def _dedupe(table: str, tuples: list) -> list:
    """Last write wins, in Python.

    ``ON CONFLICT DO UPDATE`` raises "cannot affect row a second time" when one
    statement carries the same key twice. RethinkDB primary keys are unique so
    this only ever fires on a malformed export, but a crash there would be an
    opaque server error rather than a described one.
    """
    columns = [name for name, _ in _shape(table)]
    positions = [columns.index(c) for c in _conflict_target(table)]
    seen: dict = {}
    for row in tuples:
        seen[tuple(row[p] for p in positions)] = row
    return list(seen.values())


def copy_batch(table: str, rows: list) -> int:
    """COPY one batch through a TEMP table, then upsert it into ``table``.

    Straight ``COPY "T" FROM STDIN`` has no conflict handling, so a re-run of a
    partial batch would raise on the first already-copied row and the script
    would not be resumable. Staging in a temp table costs one extra local write
    and buys ``ON CONFLICT ... DO UPDATE``.

    Encoding goes through ``db.json.dumps``, so NaN/Infinity/NUL is a
    ``ValueError`` HERE rather than an opaque server-side json syntax error.
    """
    if not rows:
        return 0
    tuples = _dedupe(table, _encode_rows(table, rows))
    shape = _shape(table)
    columns = [name for name, _ in shape]
    target = _conflict_target(table)
    if table == "PriceHistory":
        stamps = [row[columns.index("ts")] for row in tuples]
        schema.ensure_partitions(
            table,
            lo=min(stamps).astimezone(_dt.timezone.utc).date(),
            hi=max(stamps).astimezone(_dt.timezone.utc).date())
    coldef = ", ".join('"%s" %s' % (n, t) for n, t in shape)
    collist = ", ".join('"%s"' % n for n in columns)
    updates = ['"%s" = EXCLUDED."%s"' % (n, n) for n in columns if n not in target]
    if table not in _NO_UPDATED_AT:
        updates.append("updated_at = now()")
    tail = (" ON CONFLICT (%s) DO UPDATE SET %s"
            % (", ".join('"%s"' % c for c in target), ", ".join(updates))
            if updates else
            " ON CONFLICT (%s) DO NOTHING" % ", ".join('"%s"' % c for c in target))
    q = schema.quoted(table)
    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("CREATE TEMP TABLE _mig_copy (%s) ON COMMIT DROP" % coldef)
            with cur.copy("COPY _mig_copy (%s) FROM STDIN" % collist) as copy:
                for row in tuples:
                    copy.write_row(row)
            cur.execute("INSERT INTO %s (%s) SELECT %s FROM _mig_copy%s"
                        % (q, collist, collist, tail))
        conn.commit()
    return len(tuples)


def _write_backtest_rows(rows: list) -> int:
    """Split every document, then three COPYs. Returns the SOURCE row count.

    Steps are flushed PER DOCUMENT, not accumulated across the batch: the live
    sample is 3.1 MB and ~14k step entries per backtest, so holding a whole
    200-document batch's step rows in Python would be gigabytes for no gain.
    Metadata lands last, so a killed run leaves orphan step rows -- which
    assemble() reports as a missing backtest and the rerun upserts away --
    rather than a metadata row with half its steps.
    """
    metas, progress = [], []
    for doc in rows:
        meta, step_rows, prog = split_backtest_row(doc)
        bid = pg_id(brs.RESULTS_TABLE, doc)
        metas.append(meta)
        if prog is not None:
            progress.append(prog)
        steps = [{"backtest_id": bid, "kind": kind, "seq": seq,
                  "final": True, "doc": entry} for kind, seq, entry in step_rows]
        for start in range(0, len(steps), STEP_CHUNK):
            copy_batch(brs.STEPS_TABLE, steps[start:start + STEP_CHUNK])
    copy_batch(brs.RESULTS_TABLE, metas)
    copy_batch(brs.PROGRESS_TABLE, progress)
    return len(rows)


def _write_rows(table: str, rows: list) -> int:
    if table == brs.RESULTS_TABLE:
        return _write_backtest_rows(rows)
    copy_batch(table, rows)
    return len(rows)


# --------------------------------------------------------------------------
# resume state
# --------------------------------------------------------------------------

def _ensure_targets(table: str) -> list:
    names = [table, MIGRATION_STATE_TABLE]
    if table == brs.RESULTS_TABLE:
        names[1:1] = [brs.STEPS_TABLE, brs.PROGRESS_TABLE]
    return names


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def read_state(table: str) -> dict:
    schema.ensure_schema(tables=[MIGRATION_STATE_TABLE])
    return store.get(MIGRATION_STATE_TABLE, table) or {}


def _save_state(table: str, **fields) -> None:
    store.insert(MIGRATION_STATE_TABLE, dict(fields, id=table), conflict="replace")


def migrate_table(table: str, *, batch: int = DEFAULT_BATCH,
                  dry_run: bool = False) -> dict:
    """Copy one table, continuing from ``_migration_state`` if a run was killed."""
    schema.ensure_schema(tables=_ensure_targets(table))
    state = read_state(table)
    since = state.get("last_id")
    copied = int(state.get("rows_copied") or 0)
    pk = schema.spec(table).pk_field
    last = since
    for rows in export_table(table, since_id=since, batch=batch):
        last = rows[-1][pk]
        if dry_run:
            copied += len(rows)
            continue
        copied += _write_rows(table, rows)
        _save_state(table, last_id=last, rows_copied=copied)
    if not dry_run:
        _save_state(table, last_id=last, rows_copied=copied,
                    finished_at=_now_iso())
    return {"table": table, "rows": copied, "resumed_from": since,
            "dry_run": bool(dry_run)}


# --------------------------------------------------------------------------
# verify
# --------------------------------------------------------------------------

def _pg_doc(table: str, row_id: str):
    """The stored document, raw.

    NOT ``store.get``: that decodes ``TableSpec.time_fields`` back into
    ``datetime`` objects for the call sites that want them, and a datetime is
    not JSON-serialisable, so canonical() would raise on exactly the tables
    (GraphNexusNewsCache, GraphNexusTickerHistory) whose timestamps this
    migration converts. Verification compares what is STORED.
    """
    rows = store.sql('SELECT doc FROM %s WHERE id = %%s' % schema.quoted(table),
                     (row_id,))
    return rows[0]["doc"] if rows else None


def _dump_mismatch(table: str, row_id: str, source, target) -> None:
    out = pathlib.Path(MISMATCH_DIR) / table
    out.mkdir(parents=True, exist_ok=True)
    safe = "".join(c if (c.isalnum() or c in "-_.") else "_" for c in str(row_id))
    (out / ("%s.json" % safe)).write_text(
        _stdjson.dumps({"rethink": source, "postgres": target},
                       indent=2, sort_keys=True, default=str))


def verify_table(table: str, *, sample: float = 0.05) -> dict:
    """Row count both sides + canonical sha256 over a sampled fraction.

    Mismatches are written WHOLE, both documents, never summarised into a
    counter -- a count tells you something broke and nothing about what.
    """
    rethink_rows = 0
    mismatches = 0
    sampled = 0
    pk = schema.spec(table).pk_field
    for chunk in export_table(table, since_id=None, batch=DEFAULT_BATCH):
        rethink_rows += len(chunk)
        for source in chunk:
            if sample < 1.0 and random.random() > sample:
                continue
            sampled += 1
            row_id = pg_id(table, source)
            target = (brs.assemble(row_id) if table == brs.RESULTS_TABLE
                      else _pg_doc(table, row_id))
            if target is None or canonical_sha256(source) != canonical_sha256(target):
                mismatches += 1
                _dump_mismatch(table, row_id, source, target)
    return {"table": table, "rethink_rows": rethink_rows,
            "pg_rows": store.count(table), "sampled": sampled,
            "mismatches": mismatches}


def verify_ordering(table: str, *, first_n: int = 200) -> list:
    """The first N Postgres ids under ``ORDER BY id`` -- bytewise, COLLATE "C".

    ``order_by(index="id")`` orders on the ``id`` COLUMN, which is where the
    ``COLLATE "C"`` lives. Ordering on ``doc->>'id'`` would carry the cluster's
    default collation instead, which is the exact failure this check exists to
    catch.
    """
    sel = store.limit(store.order_by(store.Selection(table), index="id"),
                      int(first_n))
    sql_, params = sel.to_sql(columns="id")
    return [row["id"] for row in store.sql(sql_, params)]


def verify_ordering_parity(table: str, *, first_n: int = 200) -> dict:
    """Both stores' first N ids, and the first position where they diverge.

    Skipped for an int-keyed table. RethinkDB orders a NUMBER primary key
    numerically (3, 4, ..., 11, 12, ..., 100); a Postgres ``id`` is text and
    orders bytewise (100, 101, ..., 11, 12, ..., 3). That is a known,
    deliberate consequence of ids becoming text -- nothing reads those tables
    in id order, they are read by key and ordered by ``list_ts`` -- and
    reporting it per run would train the operator to ignore the one check that
    matters. The check that matters is the scope-suffixed text ids in
    ``_ALLOWED_STATE_TABLES``, all of which are text-keyed.
    """
    if schema.spec(table).id_type == "int":
        return {"table": table, "compared": 0, "diverged_at": None,
                "skipped": "int-keyed: ReQL orders numbers numerically, "
                           "Postgres text ids order bytewise"}
    pg_ids = verify_ordering(table, first_n=first_n)
    rk_ids: list = []
    for chunk in export_table(table, since_id=None, batch=min(first_n, 1000)):
        rk_ids.extend(pg_id(table, row) for row in chunk)
        if len(rk_ids) >= first_n:
            break
    rk_ids = rk_ids[:first_n]
    diverged = None
    for i in range(min(len(rk_ids), len(pg_ids))):
        if rk_ids[i] != pg_ids[i]:
            diverged = i
            break
    if diverged is None and len(rk_ids) != len(pg_ids):
        diverged = min(len(rk_ids), len(pg_ids))
    return {"table": table, "compared": min(len(rk_ids), len(pg_ids)),
            "diverged_at": diverged,
            "rethink": rk_ids[:8], "postgres": pg_ids[:8]}


def verify_indexes(table: str) -> list:
    """Every ReQL secondary index must have a Postgres counterpart.

    An index the BacktestResults split MOVED to another table counts as
    present when it is present there.
    """
    r, conn = rethink_conn()
    try:
        reql = set(r.db(rethink_db()).table(table).index_list().run(conn, **_RUN_OPTS))
    finally:
        conn.close()
    have = set(store.index_list(table))
    for name, moved_to in _MOVED_INDEXES.get(table, {}).items():
        if name in store.index_list(moved_to):
            have.add(name)
    return sorted(reql - have)


def verify_id_types(table: str, *, limit: int = 20) -> list:
    """Primary keys the registry's declared ``id_type`` cannot represent.

    ``schema.TableSpec.id_type="int"`` makes ``store.coerce_id`` reject a
    non-integer key, so a table declared int-keyed whose live keys are strings
    is unreadable through the store after the cutover -- ``get(table, "x")``
    raises instead of returning the row. The copy itself is unaffected (ids are
    text in Postgres either way), which is why this is checked explicitly
    rather than left to surface as a mismatch.
    """
    if schema.spec(table).id_type != "int":
        return []
    pk = schema.spec(table).pk_field
    offenders: list = []
    for chunk in export_table(table, since_id=None, batch=DEFAULT_BATCH):
        for row in chunk:
            value = row.get(pk)
            try:
                int(value)
            except (TypeError, ValueError):
                offenders.append(value)
                if len(offenders) >= limit:
                    return offenders
    return offenders


def _attested_tables() -> tuple:
    import paired_state_attest as psa
    return psa.ATTESTED_TABLES


def _rethink_rows(table: str) -> list:
    out: list = []
    for chunk in export_table(table, since_id=None, batch=DEFAULT_BATCH):
        out.extend(chunk)
    return out


def _pg_rows(table: str) -> list:
    return [r["doc"] for r in
            store.sql('SELECT doc FROM %s ORDER BY id COLLATE "C"'
                      % schema.quoted(table))]


def verify_fingerprint(for_mode: str = "backtest") -> tuple:
    """``(rethink_fp, pg_fp)`` -- paired_state_attest's start fingerprint,
    computed against each store over ATTESTED_TABLES.

    ``fingerprint_rethink`` lives HERE and not in ``paired_state_attest``: that
    module must stay RethinkDB-free, which is the whole point of the port.
    ``_VOLATILE_FIELDS`` is excluded by ``state_fingerprint`` itself, as today.
    """
    import paired_state_attest as psa
    tables = _attested_tables()
    rk = {name: _rethink_rows(name) for name in tables}
    pg = {name: _pg_rows(name) for name in tables}
    return (psa.state_fingerprint(rk, for_mode=for_mode)["bundle_sha256"],
            psa.state_fingerprint(pg, for_mode=for_mode)["bundle_sha256"])


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def _tables_arg(args) -> list:
    if args.tables:
        return [t.strip() for t in args.tables.split(",") if t.strip()]
    return rethink_table_list()


def _batch_for(table: str, requested: int) -> int:
    """BacktestResults documents are megabytes each; 2000 of them is not a
    batch, it is an out-of-memory error."""
    if table == brs.RESULTS_TABLE:
        return min(requested, BACKTEST_BATCH)
    return requested


def _run_verify(tables: list, args) -> int:
    failed = False
    for table in tables:
        report = verify_table(table, sample=args.verify_sample)
        print(report)
        if report["mismatches"] or report["rethink_rows"] != report["pg_rows"]:
            failed = True
        if args.no_parity_checks:
            continue
        try:
            missing = verify_indexes(table)
            bad_ids = verify_id_types(table)
            parity = verify_ordering_parity(table, first_n=args.ordering_sample)
        except Exception as exc:                       # noqa: BLE001
            print("  PARITY CHECKS UNAVAILABLE: %s: %s" % (type(exc).__name__, exc))
            failed = True
            continue
        if missing:
            print("  MISSING INDEXES: %s" % missing)
            failed = True
        if bad_ids:
            print("  ID TYPE MISMATCH: %s declares id_type='int' but holds %r"
                  % (table, bad_ids[:5]))
            failed = True
        if parity["diverged_at"] is not None:
            print("  ORDERING DIVERGED at %d: %s" % (parity["diverged_at"], parity))
            failed = True
    if not args.no_parity_checks:
        try:
            rk_fp, pg_fp = verify_fingerprint()
        except Exception as exc:                       # noqa: BLE001
            print("  FINGERPRINT UNAVAILABLE: %s: %s" % (type(exc).__name__, exc))
            failed = True
        else:
            if rk_fp != pg_fp:
                print("  FINGERPRINT MISMATCH: %s != %s" % (rk_fp, pg_fp))
                failed = True
            else:
                print({"fingerprint": rk_fp, "equal": True})
    if failed:
        print("VERIFY FAILED -- whole documents in %s/" % MISMATCH_DIR)
    return 1 if failed else 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="migrate_rethinkdb_to_postgres")
    p.add_argument("--tables", help="comma-separated; default: RethinkDB's table_list()")
    p.add_argument("--since-id", help="resume a table mid-stream (needs --tables)")
    p.add_argument("--batch", type=int, default=DEFAULT_BATCH,
                   help="COPY batch size (default %d; %d for BacktestResults)"
                        % (DEFAULT_BATCH, BACKTEST_BATCH))
    p.add_argument("--verify", action="store_true", help="verify only, no writes")
    p.add_argument("--verify-sample", type=float, default=0.05)
    p.add_argument("--ordering-sample", type=int, default=200)
    p.add_argument("--no-parity-checks", action="store_true",
                   help="row counts and hashes only: skip the index, id-type, "
                        "ordering and fingerprint parity checks")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)

    tables = _tables_arg(args)
    if args.verify:
        return _run_verify(tables, args)

    if args.since_id is not None:
        if len(tables) != 1:
            print("--since-id needs exactly one --tables entry")
            return 2
        schema.ensure_schema(tables=[MIGRATION_STATE_TABLE])
        _save_state(tables[0], last_id=args.since_id, rows_copied=0)

    schema.ensure_schema()
    total = 0
    for table in tables:
        report = migrate_table(table, batch=_batch_for(table, args.batch),
                               dry_run=args.dry_run)
        total += report["rows"]
        print(report)
    print({"tables": len(tables), "rows": total, "dry_run": bool(args.dry_run)})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
