"""Point-in-time writes and reads for the TR tables (spec §7.1).

Every row carries ``available_at``, an ISO-8601 UTC string. A read for a
decision at time T returns only rows with ``available_at < T`` -- strict, so
an article stamped exactly at the decision is NOT visible to it.

A row, once stored, never becomes visible later or with newer content: a
re-fetch of the same id (an ``updated_at`` edit, say) is ignored unless it
carries a strictly EARLIER ``available_at``. Keeping the first-seen content is
what stops an after-the-fact rewrite from reaching a historical prompt.

Tables are registered in ``db/schema.py``; this module never writes DDL.
``ensure_tables`` asks the db layer to create them (CREATE ... IF NOT EXISTS,
once per process) the first time a TR module touches the real store:
Postgres creates no table on demand, and no boot path ensures these.
"""
from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone

from db.errors import StoreError

PIT_TABLES = ("TrendNewsPIT", "TrendThemesPIT", "TrendMacroPIT",
              "TrendFilingsPIT", "TrendResearchLedger")
PROGRESS_TABLE = "TrendIngestProgress"
JOBS_TABLE = "TrendResearchJobs"
#: Every table a TR module reads or writes.
TR_TABLES = PIT_TABLES + (PROGRESS_TABLE, JOBS_TABLE)

_READ_BATCH = 2000
_LOOKUP_CHUNK = 500


_ENSURED = False
_ENSURE_LOCK = threading.Lock()


def ensure_tables(store=None) -> None:
    """Create the TR tables (``TR_TABLES``) through ``db.schema.ensure_schema``,
    once per process, when ``store`` is the real ``db.store`` (None means
    it). Idempotent: the DDL is CREATE ... IF NOT EXISTS under the schema's
    advisory lock. A test's FakeStore needs none and is left alone."""
    global _ENSURED
    if _ENSURED:
        return
    from db import store as real_store
    if store is not None and store is not real_store:
        return
    with _ENSURE_LOCK:
        if _ENSURED:
            return
        from db import schema
        schema.ensure_schema(tables=list(TR_TABLES))
        _ENSURED = True


def _store(store):
    """``store``, or the real ``db.store`` -- with the TR tables ensured."""
    ensure_tables(store)
    if store is not None:
        return store
    from db import store as default_store
    return default_store


def _check_table(table: str) -> None:
    if table not in PIT_TABLES:
        raise ValueError("not a TR point-in-time table: %r" % (table,))


def parse_available_at(value, *, where: str = "row") -> datetime:
    """``value`` as a tz-aware UTC datetime; ValueError unless it is an ISO
    string with a zero UTC offset ("...Z" or "...+00:00")."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("%s: available_at must be an ISO UTC string, got %r"
                         % (where, value))
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("%s: available_at is not ISO-8601: %r" % (where, value)) from None
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise ValueError("%s: available_at must be UTC, got %r" % (where, value))
    return parsed


def put_rows(table: str, rows: list, *, store=None) -> int:
    """Upsert ``rows`` by id; return how many rows were inserted or replaced.

    The whole batch is validated before anything is written: every row needs
    a non-empty string ``id`` and an ISO UTC ``available_at`` (ValueError
    otherwise). An id already stored -- or repeated in the batch -- keeps the
    version with the earliest ``available_at``; on a tie the stored (first
    seen) version wins. Raises ``StoreError`` if the store rejects any row.

    Assumes a single writer per table: the read-compare-write is not atomic.
    """
    _check_table(table)
    st = _store(store)
    chosen: dict = {}
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ValueError("%s row %d: not a dict" % (table, i))
        rid = row.get("id")
        if not isinstance(rid, str) or not rid:
            raise ValueError("%s row %d: id must be a non-empty string" % (table, i))
        at = parse_available_at(row.get("available_at"), where="%s %s" % (table, rid))
        prior = chosen.get(rid)
        if prior is None or at < prior[0]:
            chosen[rid] = (at, row)
    if not chosen:
        return 0

    ids = list(chosen)
    stored: dict = {}
    for k in range(0, len(ids), _LOOKUP_CHUNK):
        for doc in st.get_all(table, *ids[k:k + _LOOKUP_CHUNK]):
            stored[doc["id"]] = doc

    to_write = []
    for rid, (at, row) in chosen.items():
        old = stored.get(rid)
        if old is not None:
            try:
                old_at = parse_available_at(old.get("available_at"))
            except ValueError:
                old_at = None          # a malformed stored stamp is replaced
            if old_at is not None and at >= old_at:
                continue
        to_write.append(dict(row))
    if not to_write:
        return 0
    result = st.insert(table, to_write, conflict="replace")
    written = result.inserted + result.replaced
    if result.errors:
        # store.insert is partial-success (a savepoint per row): a server-side
        # rejection is counted, not raised. A dropped row must not pass as a
        # smaller count.
        raise StoreError("%s: %d of %d rows rejected on write (%d written): %s"
                         % (table, result.errors, len(to_write), written,
                            result.first_error))
    return written


def read_available(table: str, *, lo_id: str, hi_id: str,
                   decision_time: datetime, store=None) -> list:
    """Rows with ``lo_id <= id < hi_id`` and ``available_at < decision_time``,
    oldest ``available_at`` first (ties by id, bytewise)."""
    _check_table(table)
    if (not isinstance(decision_time, datetime) or decision_time.tzinfo is None
            or decision_time.utcoffset() is None):
        raise ValueError("decision_time must be a tz-aware datetime, got %r"
                         % (decision_time,))
    st = _store(store)
    sel = st.order_by(st.between(table, lo_id, hi_id), index="id")
    out = []
    for row in st.iter(sel, batch=_READ_BATCH):
        try:
            at = parse_available_at(row.get("available_at"))
        except ValueError:
            continue                   # unprovable availability never reaches a prompt
        if at < decision_time:
            out.append((at, row["id"], row))
    out.sort(key=lambda t: (t[0], t[1].encode("utf-8")))
    return [row for _, _, row in out]


# -- ingest completion markers ----------------------------------------------------
#
# TrendIngestProgress rows are bookkeeping, not research input: they carry no
# available_at and never reach a prompt, so they are written here directly
# rather than through put_rows. id = "source|key".

_KEY_END = "￿"            # above every ASCII key byte under COLLATE "C"


def _progress_id(source: str, key: str) -> str:
    if not isinstance(source, str) or not source or "|" in source:
        raise ValueError("progress source must be a non-empty string without '|': %r"
                         % (source,))
    if not isinstance(key, str) or not key:
        raise ValueError("progress key must be a non-empty string: %r" % (key,))
    return "%s|%s" % (source, key)


def mark_done(source: str, key: str, meta=None, *, store=None) -> None:
    """Record that ``key`` of ``source`` is complete (replacing any earlier
    marker). Raises ``StoreError`` if the store rejects the row."""
    rid = _progress_id(source, key)
    st = _store(store)
    row = {"id": rid, "source": source, "key": key, "meta": dict(meta or {}),
           "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    result = st.insert(PROGRESS_TABLE, [row], conflict="replace")
    if result.errors:
        raise StoreError("%s %s: marker rejected: %s"
                         % (PROGRESS_TABLE, rid, result.first_error))


def is_done(source: str, key: str, *, store=None) -> bool:
    return _store(store).get(PROGRESS_TABLE, _progress_id(source, key)) is not None


def done_keys(source: str, prefix: str = "", *, store=None) -> set:
    """Every completed key of ``source`` that starts with ``prefix``."""
    _progress_id(source, "-")                  # validates source
    lo = "%s|%s" % (source, prefix or "")
    st = _store(store)
    head = len(source) + 1
    return {row["id"][head:] for row in st.iter(st.between(PROGRESS_TABLE, lo,
                                                            lo + _KEY_END))}
