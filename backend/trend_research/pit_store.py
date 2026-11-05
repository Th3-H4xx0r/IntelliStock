"""Point-in-time writes and reads for the TR tables (spec §7.1).

Every row carries ``available_at``, an ISO-8601 UTC string. A read for a
decision at time T returns only rows with ``available_at < T`` -- strict, so
an article stamped exactly at the decision is NOT visible to it.

A row, once stored, never becomes visible later or with newer content: a
re-fetch of the same id (an ``updated_at`` edit, say) is ignored unless it
carries a strictly EARLIER ``available_at``. Keeping the first-seen content is
what stops an after-the-fact rewrite from reaching a historical prompt.

Tables are registered in ``db/schema.py``; this module never writes DDL.
"""
from __future__ import annotations

from datetime import datetime, timedelta

PIT_TABLES = ("TrendNewsPIT", "TrendThemesPIT", "TrendMacroPIT",
              "TrendFilingsPIT", "TrendResearchLedger")

_READ_BATCH = 2000
_LOOKUP_CHUNK = 500


def _store(store):
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
    seen) version wins.
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
    return result.inserted + result.replaced


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
